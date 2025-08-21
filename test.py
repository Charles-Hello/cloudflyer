import logging
from threading import Thread
import threading
import time
import argparse
import json

from curl_cffi import requests

from cloudflyer.log import apply_logging_adapter
from cloudflyer.server import main, stop_instances

CLIENT_KEY = "example_token"
EXAMPLE_TOKEN = "example_token"
BASE_URL = "http://127.0.0.1:3000"

def verify_cloudflare_challenge(result, default_proxy_str: str | None = None):
    try:
        # Extract necessary information from response
        cookies = result["response"]["cookies"]
        headers = result["response"]["headers"]
        url = result["data"]["url"]

        # Build proxies from task data if provided, otherwise fall back to default proxy string
        proxies = None
        proxy_cfg = result.get("data", {}).get("proxy")
        if proxy_cfg and isinstance(proxy_cfg, dict):
            scheme = proxy_cfg.get("scheme")
            host = proxy_cfg.get("host")
            port = proxy_cfg.get("port")
            username = proxy_cfg.get("username")
            password = proxy_cfg.get("password")
            if scheme and host and port:
                auth = f"{username}:{password}@" if username and password else ""
                proxy_url = f"{scheme}://{auth}{host}:{port}"
                proxies = {"http": proxy_url, "https": proxy_url}
        elif default_proxy_str:
            # Accept scheme://host:port or scheme://user:pass@host:port
            try:
                scheme, rest = default_proxy_str.split("://", 1)
                if "@" in rest:
                    auth, host_port = rest.split("@", 1)
                    host, port = host_port.split(":", 1)
                    proxy_url = f"{scheme}://{auth}@{host}:{port}"
                else:
                    host, port = rest.split(":", 1)
                    proxy_url = f"{scheme}://{host}:{port}"
                proxies = {"http": proxy_url, "https": proxy_url}
            except Exception:
                proxies = None

        # Use curl_cffi to send request
        response = requests.get(
            url,
            cookies=cookies,
            headers={"User-Agent": headers["User-Agent"]},
            impersonate="chrome",
            allow_redirects=True,
            proxies=proxies,
        )

        # Check if response contains success marker
        return "Captcha is passed successfully!" in response.text
    except (KeyError, TypeError):
        return False


def create_task(data):
    headers = {"Content-Type": "application/json"}
    response = requests.post(f"{BASE_URL}/createTask", json=data, headers=headers)
    try:
        return response.json()
    except Exception:
        return {"status": "error", "http_status": response.status_code, "text": response.text}


def get_task_result(task_id, client_key=EXAMPLE_TOKEN):
    headers = {"Content-Type": "application/json"}

    data = {"clientKey": client_key, "taskId": task_id}

    response = requests.post(
        f"{BASE_URL}/getTaskResult",
        json=data,
        headers=headers,
    )
    return response.json()


def start_server(use_hazetunnel=True, headless=False, default_proxy: str | None = None, allow_local_proxy: bool = False):
    ready = threading.Event()
    argl = ["-K", EXAMPLE_TOKEN]
    if not use_hazetunnel:
        argl.append("--no-hazetunnel")
    if headless:
        argl.append("--headless")
    if default_proxy:
        argl += ["--default-proxy", default_proxy]
    if allow_local_proxy:
        argl.append("--allow-local-proxy")
    
    t = Thread(
        target=main,
        kwargs={
            "argl": argl,
            "ready": ready,
            "log": False,
        },
        daemon=True,
    )
    t.start()
    # Use interruptible wait so Ctrl+C works
    while not ready.is_set():
        try:
            ready.wait(timeout=0.1)
        except KeyboardInterrupt:
            raise
    return t


def poll_task_result(task_id, client_key) -> dict:
    while True:
        cf_response = get_task_result(task_id, client_key)
        if cf_response["status"] == "completed":
            return cf_response["result"]
        time.sleep(3)


def cloudflare_challenge(default_proxy_str=None, use_hazetunnel=True, headless=False, screencast_path=None, allow_local_proxy=False):
    start_server(use_hazetunnel, headless, default_proxy=default_proxy_str, allow_local_proxy=allow_local_proxy)

    data = {
        "clientKey": EXAMPLE_TOKEN,
        "type": "CloudflareChallenge",
        "url": "https://2captcha.com/demo/cloudflare-turnstile-challenge",
    }
    
    # No per-task proxy; rely on default proxy if provided
    
    # Add screencast configuration if provided
    if screencast_path:
        data["screencast_path"] = screencast_path

    task_info = create_task(data)
    if "taskId" not in task_info:
        print(f"createTask failed: {task_info}")
        return
    result = poll_task_result(task_info["taskId"], data["clientKey"])
    print(f"Challenge result:\n{json.dumps(result, indent=2)}")

    success = verify_cloudflare_challenge(result, default_proxy_str=default_proxy_str)
    print(f"\nChallenge verification result:\n{success}")


def turnstile(default_proxy_str=None, use_hazetunnel=True, headless=False, screencast_path=None, allow_local_proxy=False):
    start_server(use_hazetunnel, headless, default_proxy=default_proxy_str, allow_local_proxy=allow_local_proxy)

    data = {
        "clientKey": EXAMPLE_TOKEN,
        "type": "Turnstile",
        "url": "https://www.coronausa.com",
        "siteKey": "0x4AAAAAAAH4-VmiV_O_wBN-",
    }

    # No per-task proxy; rely on default proxy if provided
    
    # Add screencast configuration if provided
    if screencast_path:
        data["screencast_path"] = screencast_path

    print("Task:")
    print(json.dumps(data, indent=2))
    task_info = create_task(data)
    if "taskId" not in task_info:
        print(f"createTask failed: {task_info}")
        return
    result = poll_task_result(task_info["taskId"], data["clientKey"])
    print(f"Turnstile result:\n{json.dumps(result, indent=2)}")


    response = result.get("response")
    if response:
        token = response.get("token")
    else:
        token = None
    if token:
        print(f"\nTurnstile token:\n{token}")

def recapcha_invisible(default_proxy_str=None, use_hazetunnel=True, headless=False, screencast_path=None, allow_local_proxy=False):
    start_server(use_hazetunnel, headless, default_proxy=default_proxy_str, allow_local_proxy=allow_local_proxy)

    data = {
        "clientKey": EXAMPLE_TOKEN,
        "type": "RecaptchaInvisible",
        "url": "https://antcpt.com/score_detector",
        "siteKey": "6LcR_okUAAAAAPYrPe-HK_0RULO1aZM15ENyM-Mf",
        "action": "homepage",
    }

    # No per-task proxy; rely on default proxy if provided
    
    # Add screencast configuration if provided
    if screencast_path:
        data["screencast_path"] = screencast_path

    task_info = create_task(data)
    if "taskId" not in task_info:
        print(f"createTask failed: {task_info}")
        return
    result = poll_task_result(task_info["taskId"], data["clientKey"])
    print(f"Challenge result:\n{json.dumps(result, indent=2)}")

def parse_proxy_string(proxy_str):
    """Parse proxy string in format scheme://host:port or scheme://user:pass@host:port"""
    if not proxy_str:
        return None
    
    try:
        from cloudflyer.server import ProxyConfig
        proxy_config = ProxyConfig.from_string(proxy_str)
        return proxy_config.to_dict()
    except ValueError as e:
        raise ValueError(f"Invalid proxy format: {e}")

def main_cli():
    parser = argparse.ArgumentParser(description="Challenge solver CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Add proxy argument to each command
    for cmd in ["turnstile", "cloudflare", "recaptcha"]:
        parser_cmd = subparsers.add_parser(cmd, help=f"Solve {cmd.replace('_', ' ')} challenge")
        parser_cmd.add_argument("-x", "--proxy", help="Default proxy (scheme://host:port or scheme://user:pass@host:port)")
        parser_cmd.add_argument("-v", "--verbose", help="Show verbose output", action="store_true")
        parser_cmd.add_argument("-L", "--headless", action="store_true", help="Run browser in headless mode")
        parser_cmd.add_argument("--no-hazetunnel", action="store_true", help="Skip hazetunnel and connect directly to pproxy upstream")
        parser_cmd.add_argument("--allow-local-proxy", action="store_true", help="Allow localhost (127.0.0.1/localhost) proxies")
        parser_cmd.add_argument("-s", "--screencast", help="Enable screencast recording and specify save path (e.g., ./recordings)")

    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return

    # Prepare default proxy values
    default_proxy_str = args.proxy if args.proxy else None
    # Validate format early, and keep parsed form for potential future needs
    _ = parse_proxy_string(default_proxy_str) if default_proxy_str else None
    use_hazetunnel = not args.no_hazetunnel
    headless = args.headless
    screencast_path = args.screencast
    allow_local_proxy = getattr(args, 'allow_local_proxy', False)
    
    if args.verbose:
        apply_logging_adapter([
            ('http.*->.*', logging.DEBUG),
            ('server disconnect', logging.DEBUG),
            ('client disconnect', logging.DEBUG),
            ('server connect', logging.DEBUG),
            ('client connect', logging.DEBUG),
        ], level=10)
        logging.getLogger('hpack').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)

    # Execute corresponding function based on command
    try:
        if args.command == "turnstile":
            turnstile(default_proxy_str, use_hazetunnel, headless, screencast_path, allow_local_proxy)
        elif args.command == "cloudflare":
            cloudflare_challenge(default_proxy_str, use_hazetunnel, headless, screencast_path, allow_local_proxy)
        elif args.command == "recaptcha":
            recapcha_invisible(default_proxy_str, use_hazetunnel, headless, screencast_path, allow_local_proxy)
        else:
            parser.print_help()
    finally:
        stop_instances()

if __name__ == "__main__":
    main_cli()
