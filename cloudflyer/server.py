import logging
import sys
import uuid
from typing import List, Optional, Union
import argparse
import threading
from cachetools import TTLCache
from datetime import timedelta
import re

from pydantic import BaseModel, field_validator, Field
from fastapi import FastAPI, HTTPException
import uvicorn

from .pool import InstancePool
from .log import apply_logging_adapter
from .downloader import ensure_tool

app = FastAPI()
logger = logging.getLogger(__name__)

instance_pool = None
tasks = TTLCache(maxsize=10000, ttl=timedelta(days=1).total_seconds())
client_key = None

class ProxyConfig(BaseModel):
    scheme: str = Field(..., description="Proxy scheme (http, https, socks4, socks5)")
    host: str = Field(..., description="Proxy host/IP address")
    port: int = Field(..., ge=1, le=65535, description="Proxy port (1-65535)")
    username: Optional[str] = Field(None, description="Proxy username for authentication")
    password: Optional[str] = Field(None, description="Proxy password for authentication")

    @field_validator('scheme')
    @classmethod
    def validate_scheme(cls, v: str) -> str:
        """Validate and normalize proxy scheme"""
        v = v.lower().strip()
        
        # Convert socks5h to socks5 (automatic conversion)
        if v == 'socks5h':
            logger.info("Converting socks5h to socks5 - socks5h is not supported")
            v = 'socks5'
        
        # Supported proxy schemes
        supported_schemes = {'http', 'https', 'socks4', 'socks5'}
        if v not in supported_schemes:
            raise ValueError(f"Unsupported proxy scheme '{v}'. Supported schemes: {', '.join(supported_schemes)}")
        
        return v

    @field_validator('host')
    @classmethod
    def validate_host(cls, v: str) -> str:
        """Validate proxy host"""
        v = v.strip()
        if not v:
            raise ValueError("Proxy host cannot be empty")
        
        # Basic validation for IP address or hostname
        # Allow IPv4, IPv6, and domain names
        ip_pattern = re.compile(
            r'^(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
            r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
        )
        ipv6_pattern = re.compile(r'^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$')
        domain_pattern = re.compile(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$')
        
        if not (ip_pattern.match(v) or ipv6_pattern.match(v) or domain_pattern.match(v) or v == 'localhost'):
            raise ValueError(f"Invalid proxy host format: {v}")
        
        return v

    @classmethod
    def from_string(cls, proxy_str: str) -> 'ProxyConfig':
        """Parse proxy from string format: scheme://[username:password@]host:port"""
        if not proxy_str or not isinstance(proxy_str, str):
            raise ValueError("Proxy string cannot be empty")
        
        proxy_str = proxy_str.strip()
        
        # Parse scheme
        if '://' not in proxy_str:
            raise ValueError("Proxy string must contain scheme (e.g., http://host:port)")
        
        scheme, rest = proxy_str.split('://', 1)
        
        # Parse authentication and host:port
        username = None
        password = None
        
        if '@' in rest:
            auth_part, host_port = rest.rsplit('@', 1)
            if ':' in auth_part:
                username, password = auth_part.split(':', 1)
            else:
                raise ValueError("Invalid authentication format. Use username:password@host:port")
        else:
            host_port = rest
        
        # Parse host and port
        if ':' not in host_port:
            raise ValueError("Port must be specified in proxy string")
        
        # Handle IPv6 addresses in brackets
        if host_port.startswith('[') and ']:' in host_port:
            host, port_str = host_port.rsplit(']:', 1)
            host = host[1:]  # Remove leading [
        else:
            host, port_str = host_port.rsplit(':', 1)
        
        try:
            port = int(port_str)
        except ValueError:
            raise ValueError(f"Invalid port number: {port_str}")
        
        return cls(
            scheme=scheme,
            host=host,
            port=port,
            username=username,
            password=password
        )

    def to_url(self) -> str:
        """Convert proxy config to URL string"""
        auth = f"{self.username}:{self.password}@" if self.username and self.password else ""
        return f"{self.scheme}://{auth}{self.host}:{self.port}"

    def to_dict(self) -> dict:
        """Convert to dictionary for backward compatibility"""
        result = {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port
        }
        if self.username:
            result["username"] = self.username
        if self.password:
            result["password"] = self.password
        return result

class LinkSocksConfig(BaseModel):
    url: str
    token: str

class CreateTaskRequest(BaseModel):
    clientKey: str = Field(..., description="Client API key for authentication")
    type: str = Field(..., description="Task type (CloudflareChallenge, RecaptchaInvisible, Turnstile)")
    url: str = Field(..., description="Target URL to process")
    userAgent: Optional[str] = Field(None, description="Custom User-Agent string")
    proxy: Optional[Union[ProxyConfig, str]] = Field(None, description="Proxy configuration (object or string)")
    siteKey: Optional[str] = Field(None, description="Site key for captcha challenges")
    action: Optional[str] = Field(None, description="Action for RecaptchaInvisible")
    content: bool = Field(False, description="Return page content")
    linksocks: Optional[LinkSocksConfig] = Field(None, description="LinkSocks tunnel configuration")
    screencast_path: Optional[str] = Field(None, description="Path for screencast recording")

    @field_validator('type')
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate task type"""
        supported_types = {"CloudflareChallenge", "RecaptchaInvisible", "Turnstile"}
        if v not in supported_types:
            raise ValueError(f"Unsupported task type '{v}'. Supported types: {', '.join(supported_types)}")
        return v

    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format"""
        v = v.strip()
        if not v:
            raise ValueError("URL cannot be empty")
        
        # Basic URL validation
        url_pattern = re.compile(
            r'^https?://'  # http:// or https://
            r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
            r'localhost|'  # localhost...
            r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
            r'(?::\d+)?'  # optional port
            r'(?:/?|[/?]\S+)$', re.IGNORECASE)
        
        if not url_pattern.match(v):
            raise ValueError(f"Invalid URL format: {v}")
        
        return v

    @field_validator('proxy')
    @classmethod
    def validate_proxy(cls, v: Union[ProxyConfig, str, None]) -> Optional[ProxyConfig]:
        """Validate and convert proxy configuration"""
        if v is None:
            return None
        
        if isinstance(v, str):
            # Parse proxy string and return ProxyConfig object
            try:
                return ProxyConfig.from_string(v)
            except ValueError as e:
                raise ValueError(f"Invalid proxy string: {e}")
        elif isinstance(v, dict):
            # Convert dict to ProxyConfig
            try:
                return ProxyConfig(**v)
            except Exception as e:
                raise ValueError(f"Invalid proxy configuration: {e}")
        elif isinstance(v, ProxyConfig):
            return v
        else:
            raise ValueError("Proxy must be a string, dict, or ProxyConfig object")

    @field_validator('siteKey')
    @classmethod
    def validate_site_key(cls, v: Optional[str], info) -> Optional[str]:
        """Validate siteKey based on task type"""
        if info.data.get('type') == 'Turnstile' and not v:
            raise ValueError("siteKey is required for Turnstile tasks")
        return v

    @field_validator('action')
    @classmethod
    def validate_action(cls, v: Optional[str], info) -> Optional[str]:
        """Validate action based on task type"""
        if info.data.get('type') == 'RecaptchaInvisible' and v is not None:
            if not v.strip():
                raise ValueError("action cannot be empty for RecaptchaInvisible tasks")
        return v

class TaskResultRequest(BaseModel):
    clientKey: str
    taskId: Optional[str] = None

@app.post("/createTask")
async def create_task(request: CreateTaskRequest):
    # Validate clientKey
    if request.clientKey != client_key:
        raise HTTPException(status_code=403, detail="Invalid clientKey")
    
    # Convert the validated request to dict for processing
    data = request.model_dump()
    data.pop("clientKey", None)
    
    # Convert ProxyConfig to dict if present
    if data.get("proxy") and isinstance(data["proxy"], ProxyConfig):
        data["proxy"] = data["proxy"].to_dict()
    
    task_id = str(uuid.uuid4())
    tasks[task_id] = {
        "status": "processing",
        "data": data,
        "result": None
    }
    
    threading.Thread(target=process_task, args=(task_id,), daemon=True).start()
    
    logger.info(f"Created task for: {task_id}.")
    
    return {"taskId": task_id}

@app.post("/getTaskResult")
async def get_task_result(request: TaskResultRequest):
    # Validate clientKey
    if request.clientKey != client_key:
        raise HTTPException(status_code=403, detail="Invalid clientKey")
    if request.taskId not in tasks:
        return {
            "status": "error",
            "error": "Task not found",
            "result": None
        }

    task = tasks[request.taskId]
    return {
        "status": task["status"],
        "result": task["result"] if task["status"] == "completed" else None,
    }
    
def stop_instances():
    if instance_pool:
        instance_pool.stop()

def process_task(task_id: str):
    task = tasks[task_id]
    task_data = task["data"]
    try:
        result = instance_pool.run_task(task_data)
        task["result"] = result
        if result.get("success", False):
            logger.info(f"Job finished successfully: {task_id}.")
        else:
            logger.info(f"Job failed: {task_id} ({result.get('error', 'unknown reason')}).")
    finally:
        task["status"] = "completed"

def main(argl: List[str] = None, ready: threading.Event = None, log: bool = True):
    global instance_pool, client_key
    
    if argl is None:
        argl = sys.argv[1:]
    
    if log:
        apply_logging_adapter([
            ('http.*->.*', logging.DEBUG),
            ('server disconnect', logging.DEBUG),
            ('client disconnect', logging.DEBUG),
            ('server connect', logging.DEBUG),
            ('client connect', logging.DEBUG),
        ], level=10)
        logging.getLogger('hpack').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)
    
    parser = argparse.ArgumentParser(description="Cloudflare bypass API server")
    parser.add_argument("-K", "--clientKey", required=True, help="Client API key")
    parser.add_argument("-M", "--maxTasks", type=int, default=1, help="Maximum concurrent tasks")
    parser.add_argument("-P", "--port", type=int, default=3000, help="Server listen port")
    parser.add_argument("-H", "--host", default="localhost", help="Server listen host")
    parser.add_argument("-T", "--timeout", type=int, default=120, help="Maximum task timeout in seconds")
    parser.add_argument("-L", "--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("-V", "--vdisplay", action="store_true", help="Run browser in virtual display mode")
    parser.add_argument("-D", "--no-hazetunnel", action="store_true", help="Skip hazetunnel JA3 fingerprint spoofing")
    parser.add_argument("-X", "--default-proxy", help="Default upstream proxy, format: scheme://host:port or scheme://user:pass@host:port")
    parser.add_argument("-A", "--allow-local-proxy", action="store_true", help="Allow localhost proxies (127.0.0.1/localhost). Disabled by default.")
    
    args = parser.parse_args(argl)

    # Ensure external helpers are available at startup with logs
    try:
        if not args.no_hazetunnel:
            ensure_tool("hazetunnel")
        ensure_tool("linksocks")
    except Exception:
        # Do not crash the server on download failure; downstream will raise when used
        pass

    # Store the expected clientKey for request validation
    client_key = args.clientKey
    
    if args.vdisplay:
        from pyvirtualdisplay import Display

        display = Display(visible=False, size=(1024, 768))
        display.start()

    # Parse default proxy string if provided
    def _parse_proxy_string(proxy_str):
        if not proxy_str:
            return None
        try:
            proxy_config = ProxyConfig.from_string(proxy_str)
            return proxy_config.to_dict()
        except ValueError as e:
            raise ValueError(f"Invalid --default-proxy format: {e}")

    default_proxy_cfg = _parse_proxy_string(args.default_proxy) if args.default_proxy else None

    try:
        instance_pool = InstancePool(
            size=args.maxTasks,
            timeout=args.timeout,
            use_hazetunnel=not args.no_hazetunnel,
            headless=args.headless,
            default_proxy=default_proxy_cfg,
            allow_local_proxy=args.allow_local_proxy,
        )
        instance_pool.init_instances()
        
        if ready:
            ready.set()
        
        try:
            uvicorn.run(app, host=args.host, port=args.port, log_config=None, log_level=None)
        finally:
            instance_pool.stop()
    finally:
        if args.vdisplay:
            display.stop()

if __name__ == '__main__':
    main()