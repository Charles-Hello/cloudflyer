import asyncio
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
from threading import Event
import time
from urllib.parse import urlparse
from importlib import resources
import urllib3

# Disable InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import appdirs
from cachetools import TTLCache
from mitmproxy.http import HTTPFlow, Response
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError, BrowserConnectError

from .mitm import MITMProxy
from .utils import get_free_port, get_net_info
from .bypasser import CloudflareBypasser
from . import html as html_res

logger = logging.getLogger(__name__)

COMMON_ARGUMENTS = [
    "-no-first-run",
    "-force-color-profile=srgb",
    "-metrics-recording-only",
    "-disable-background-mode",
    "-disable-features=FlashDeprecationWarning,EnablePasswordsAccountStorage",
    # "-disable-gpu", # Detected by Cloudflare
    "-accept-lang=en-US",
    "--window-size=512,512",
    "--disable-infobars",
    "--window-name=Cloudflyer",
    "--disable-sync",
    "--app=https://internals.cloudflyer.com/index",
    "--lang=en",
    # "--disable-setuid-sandbox", # Detected by Cloudflare
    # "--disable-dev-shm-usage", # Detected by Cloudflare
    "--disable-search-engine-choice-screen",
    "--no-zygote",
]

NON_HEADLESS_ARGUMENTS = [
    "-password-store=basic",
    "-use-mock-keychain",
    "-export-tagged-pdf",
    "-no-default-browser-check",
]

DEFAULT_ARGUMENTS = COMMON_ARGUMENTS + NON_HEADLESS_ARGUMENTS

HEADLESS_ARGUMENTS = COMMON_ARGUMENTS

DEFAULT_BROWSER_PATH = os.getenv("CHROME_PATH", None)
DEFAULT_CERT_PATH = Path(os.getenv("CERT_PATH", appdirs.user_data_dir("cloudflyer"))) / 'certs'

class MITMAddon:
    index_html_templ: str = None
    turnstile_html_templ: str = None
    cloudflare_challenge_html_templ: str = None
    recaptcha_invisible_html_templ: str = None
    
    def _get_index_html(self):
        if not self.turnstile_html_templ:
            with resources.files(html_res).joinpath('CloudFlyer.html').open('r') as f:
                self.__class__.index_html_templ = f.read()
        return self.index_html_templ
    
    def _get_cloudflare_challenge_html(self, script: str):
        if not self.cloudflare_challenge_html_templ:
            with resources.files(html_res).joinpath('CloudflareChallenge.html').open('r') as f:
                self.__class__.cloudflare_challenge_html_templ = f.read()
        return self.cloudflare_challenge_html_templ.replace("![script]!", script)
    
    def _get_turnstile_html(self, site_key: str):
        if not self.turnstile_html_templ:
            with resources.files(html_res).joinpath('Turnstile.html').open('r') as f:
                self.__class__.turnstile_html_templ = f.read()
        return self.turnstile_html_templ.replace("![sitekey]!", site_key)
    
    def _get_recaptcha_invisible_html(self, site_key: str, action: str):
        if not self.recaptcha_invisible_html_templ:
            with resources.files(html_res).joinpath('RecaptchaInvisible.html').open('r') as f:
                self.__class__.recaptcha_invisible_html_templ = f.read()
        return self.recaptcha_invisible_html_templ.replace("![sitekey]!", site_key).replace("![action]!", action)
    
    def __init__(self) -> None:
        self.url_cache = TTLCache(maxsize=10000, ttl=timedelta(hours=1).total_seconds())
        self.reset()
        self.ready = Event()
        
    def running(self):
        logger.debug("MITM addon is ready to handle.")
        self.ready.set()
        
    def reset(self):
        self.cloudflare_challenge_target_host = None
        self.recaptcha_invisible_target_host = None
        self.recaptcha_site_key = None
        self.recaptcha_action = None
        self.turnstile_target_host = None
        self.turnstile_site_key = None
        self.user_agent = None
        self.result = None
    
    def requestheaders(self, flow: HTTPFlow):
        # Modify request UA
        if self.user_agent:
            flow.request.headers["User-Agent"] = self.user_agent
            
        if any(i in flow.request.pretty_url for i in [
            'android.clients.google.com',
            'optimizationguide-pa.googleapis.com',
            'clients2.google.com',
            'safebrowsingohttpgateway.googleapis.com',
            'clientservices.googleapis.com',
        ]):
            flow.response = Response.make(
                404,
                b"CloudFlyer blocked chrome updates and other resources.",
                {"Content-Type": "text/plain"}
            )
            return
    
    def request(self, flow: HTTPFlow):
        # Remove "Headless" from User-Agent header
        if "User-Agent" in flow.request.headers:
            user_agent = flow.request.headers["User-Agent"]
            if "Headless" in user_agent:
                # Remove all instances of "Headless" from the User-Agent string
                cleaned_user_agent = user_agent.replace("Headless", "")
                flow.request.headers["User-Agent"] = cleaned_user_agent
                logger.debug(f"Cleaned User-Agent: {user_agent} -> {cleaned_user_agent}")

        # Show turnstile solving page
        if self.turnstile_target_host and self.turnstile_target_host in flow.request.pretty_host:
            flow.response = Response.make(
                200,
                self._get_turnstile_html(self.turnstile_site_key).encode(),
                {"Content-Type": "text/html"},
            )
            logger.debug("Returning turnstile html using MITM.")
        
        # Show index page
        elif 'internals.cloudflyer.com/ready' in flow.request.pretty_url:
            flow.response = Response.make(
                200,
                b"OK",
                {"Content-Type": "text/plain"},
            )
        
        # Show index page
        elif 'internals.cloudflyer.com/index' in flow.request.pretty_url:
            flow.response = Response.make(
                200,
                self._get_index_html().encode(),
                {"Content-Type": "text/html"},
            )
        
        # Catch result posted from page
        elif 'internals.cloudflyer.com/result' in flow.request.pretty_url:
            if flow.request.method == 'OPTIONS':
                flow.response = Response.make(
                    204,
                    b"",
                    {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS", "Access-Control-Allow-Headers": "Content-Type, Authorization"},
                )
            else:
                self.result = flow.request.data.content.decode()
                flow.response = Response.make(
                    200,
                    b"OK",
                    {"Content-Type": "text/plain"}
                )
                logger.debug("Caught turnstile token using MITM.")

        elif flow.request.pretty_url in self.url_cache:
            # Replay cached response
            cached_response = self.url_cache[flow.request.pretty_url]
            flow.response = cached_response
            logger.debug(f"Replayed cached response for: {flow.request.pretty_url}")
            
        if not flow.response:
            logger.debug(f"MITM caught and continue request to: {flow.request.pretty_url}")
        else:
            logger.debug(f"MITM caught and provided response for: {flow.request.pretty_url}")
    
    def responseheaders(self, flow: HTTPFlow):
        # Block certain resource
        if flow.response.headers:
            # Block large responses (>5MB)
            content_length = int(flow.response.headers.get("Content-Length", "0"))
            if content_length > 5 * 1024 * 1024:  # 30MB in bytes
                flow.response = Response.make(
                    404,
                    b"CloudFlyer blocked large file",
                    {"Content-Type": "text/plain"}
                )
                return
        
        # Return error for challenge redirection to another host
        if self.cloudflare_challenge_target_host and self.cloudflare_challenge_target_host in flow.request.pretty_host:
            if flow.response.status_code in [301, 302, 303, 307, 308]:
                location = flow.response.headers.get("Location", "")
                if location:
                    redirect_host = urlparse(location).hostname
                    if redirect_host and redirect_host != flow.request.pretty_host:
                        flow.response = Response.make(
                            403,
                            b"CloudFlyer blocked cross-domain redirection",
                            {"Content-Type": "text/plain"}
                        )
    
    def response(self, flow: HTTPFlow):
        # Show cloudflare challenge solving page
        if self.cloudflare_challenge_target_host and self.cloudflare_challenge_target_host in flow.request.pretty_host:
            if flow.response.headers:
                try:
                    content = flow.response.content.decode()
                except UnicodeDecodeError:
                    pass
                else:
                    if '<body class="no-js">' in content:
                        script = content.split('<body class="no-js">')[1].split("</body>")[0]
                        flow.response = Response.make(
                            200,
                            self._get_cloudflare_challenge_html(script).encode(),
                            {"Content-Type": "text/html"},
                        )
                    elif '<title>Just a moment...</title>' in content:
                        script = content.split('<body>')[1].split("</body>")[0]
                        flow.response = Response.make(
                            200,
                            self._get_cloudflare_challenge_html(script).encode(),
                            {"Content-Type": "text/html"},
                        )
                    elif flow.response.headers.get("Content-Type", "") == 'text/html':
                        flow.response = Response.make(
                            200,
                            self._get_cloudflare_challenge_html('<div class="title2">Cloudflare Solved</div>').encode(),
                            {"Content-Type": "text/html"},
                        )

        # Show recaptcha challenge solving page
        if self.recaptcha_invisible_target_host and self.recaptcha_invisible_target_host in flow.request.pretty_host:
            flow.response = Response.make(
                200,
                self._get_recaptcha_invisible_html(self.recaptcha_site_key, self.recaptcha_action).encode(),
                {"Content-Type": "text/html"},
            )
            
        # Cache static urls
        if flow.request.pretty_url.startswith("https://challenges.cloudflare.com/turnstile/v0/"):
            url = flow.request.pretty_url
            self.url_cache[url] = flow.response
            logger.debug(f"Cached static url: {url}")

class Instance:
    def __init__(
        self,
        arguments: list = None,
        browser_path: str = DEFAULT_BROWSER_PATH,
        certdir: str = str(DEFAULT_CERT_PATH),
        use_hazetunnel: bool = True,
        headless: bool = False,
        default_proxy: dict = None,
        allow_local_proxy: bool = False,
    ):
        self.browser_path = browser_path
        self.certdir = certdir
        self.headless = headless
        self.default_proxy_config = default_proxy
        self.allow_local_proxy = allow_local_proxy
        
        # Select appropriate default arguments based on headless mode
        if arguments is None:
            self.arguments = HEADLESS_ARGUMENTS if headless else DEFAULT_ARGUMENTS
        else:
            self.arguments = arguments
        self.driver: ChromiumPage = None
        self.addon = MITMAddon()
        self.mitm_port = get_free_port()
        self.mitm = MITMProxy(
            port=self.mitm_port,
            certdir=self.certdir,
            addons=[self.addon],
            use_hazetunnel=use_hazetunnel,
            default_external_proxy=self.default_proxy_config,
        )
        self.screencast_active = False
        
    def _start_screencast(self, screencast_path):
        """Start screen recording"""
        try:
            import os
            os.makedirs(screencast_path, exist_ok=True)
            
            self.driver.screencast.set_save_path(screencast_path)
            self.driver.screencast.set_mode.video_mode()
            self.driver.screencast.start()
            self.screencast_active = True
            logger.info(f"Screencast started, saving to: {screencast_path}")
            return True
        except Exception as e:
            logger.warning(f"Failed to start screencast: {e}")
            self.screencast_active = False
            return False
    
    def _stop_screencast(self, suffix="", task_type="unknown"):
        """Unified screen recording stop method"""
        if not self.screencast_active:
            return None
            
        try:
            if self.driver:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                video_name = f"{task_type}_{timestamp}{suffix}.mp4"
                screencast_file = self.driver.screencast.stop(video_name=video_name)
                logger.info(f"Screencast saved to: {screencast_file}")
                return screencast_file
            else:
                # Driver not available, just stop silently
                return None
        except Exception as e:
            logger.debug(f"Failed to stop screencast: {e}")
            return None
        finally:
            self.screencast_active = False


    def start(self):
        # Initialize driver with MITM proxy
        self.mitm.start()
        # Check if MITM thread is still alive and wait for ready signal
        for i in range(100):  # Check for 10 seconds total
            if self.addon.ready.wait(timeout=0.1):
                break
            # If MITM thread died, don't wait anymore
            if not self.mitm.thread.is_alive():
                raise RuntimeError("MITM proxy failed to start.")
        else:
            raise RuntimeError("MITM proxy failed to start.")
        options = ChromiumOptions().auto_port()
        options.set_paths(browser_path=self.browser_path)
        options.ignore_certificate_errors(True)
        if self.headless:
            options.set_argument("--headless")
            logger.warning("Headless mode is enabled, but it may not work as expected for Captcha bypass.")
        for argument in self.arguments:
            options.set_argument(argument)
        options.set_proxy(f"http://127.0.0.1:{self.mitm_port}")
        options.set_retry(20, 0.5)
        
        # Check for BrowserConnectError and run debugger if needed
        try:
            logger.debug("Checking DrissionPage browser connection...")
            self.driver = ChromiumPage(addr_or_opts=options, timeout=0.5)
        except BrowserConnectError as e:
            logger.warning(f"Browser connection failed: {e}")
            logger.info("Running DrissionPage debugger to diagnose the issue...")

            from .drission_debugger import run_drission_diagnosis

            run_drission_diagnosis(
                browser_path=self.browser_path,
                proxy_port=self.mitm_port,
                headless=self.headless
            )
            
            # Re-raise the original error with diagnostic information
            raise BrowserConnectError(f"Browser connection failed. Diagnosis completed. Check logs for recommendations. Original error: {e}") from e
        logger.debug("ChromiumPage driver initialized with MITM proxy.")
        self.driver.get('https://internals.cloudflyer.com/index')

    def stop(self):
        # Stop screencast first if active
        self._stop_screencast(suffix="_shutdown")
                
        try:
            if self.driver:
                self.driver.quit()
                self.driver = None
        except:
            pass
        if self.mitm:
            self.mitm.stop()
            self.mitm = None

    def task_main(self, task: dict, timeout: float):
        try:
            return asyncio.run(self._task_main(task, timeout=timeout))
        except Exception as e:
            # Stop screencast if it was active during exception
            task_type = task.get("type", "unknown")
            self._stop_screencast(suffix="_error", task_type=task_type)
                    
            if isinstance(e, PageDisconnectedError):
                reason = "Timeout to solve the captcha, maybe you have set up the wrong proxy, or you are using a risky network, or the server is not operational."
            else:
                reason = "Unknown error, please retry later."
                logger.exception(f"Error occurs for task: {e.__class__.__name__}: {e}")
            return {"success": False, "code": 500, "error": reason, "data": task}
        finally:
            with resources.files('cloudflyer.html').joinpath('CloudFlyer.html').open('r') as f:
                self.__class__.turnstile_html_templ = f.read()
            try:
                self.driver.get('https://internals.cloudflyer.com/index')
            except (AttributeError, PageDisconnectedError):
                pass

    async def _task_main(self, task: dict, timeout: float):
        start_time = datetime.now()
        self.addon.reset()
        
        # Clear browser state
        self.driver.clear_cache()
        
        # Setup screencast if enabled
        screencast_path = task.get("screencast_path")
        if screencast_path:
            self._start_screencast(screencast_path)
        
        # Ensure URL starts with http:// or https://
        if not task["url"].startswith(("http://", "https://")):
            task["url"] = "http://" + task["url"]

        proxy = task.get("proxy")
        if proxy:
            # Block local proxies unless allowed
            try:
                host_val = (proxy.get("host") or "").lower()
                if (host_val in {"127.0.0.1", "localhost"}) and (not self.allow_local_proxy):
                    return {
                        "success": False,
                        "code": 400,
                        "error": "Local proxies are disabled. Use --allow-local-proxy to enable.",
                        "data": task,
                    }
            except Exception:
                pass
        linksocks = task.get("linksocks")
        
        if proxy and isinstance(proxy, dict):
            logger.info(f"Task will be executed using proxy chained after default external proxy: {proxy}")
            # Pass only task proxy; MITM will prepend default external proxy if present
            await self.mitm.update_proxy(proxy)
        elif linksocks and isinstance(linksocks, dict):
            linksocks_url = linksocks.get("url", None)
            linksocks_token = linksocks.get("token", None)
            if linksocks_url and linksocks_token:
                from .linksocks import LinkSocks

                links = LinkSocks()
                port = get_free_port()
                if not links.start(linksocks_token, linksocks_url, port):
                    return {
                        "success": False,
                        "code": 500,
                        "response": None,
                        "error": "Fail to connect to the linksocks proxy.",
                        "data": task,
                    }
                
                proxy_config = {"scheme": "socks5", "host": "127.0.0.1", "port": port}
                logger.info(f"Task will be executed using linksocks proxy: {proxy_config}")
                await self.mitm.update_proxy(proxy_config)
            else:
                return {
                    "success": False,
                    "code": 500,
                    "response": None,
                    "error": "Either linksocks.url or linksocks.token is not provided.",
                    "data": task,
                }
        else:
            if self.default_proxy_config:
                logger.info("Task will be executed with default external proxy as enforced first hop.")
            else:
                logger.info("Task will be executed without proxy.")
            # None means MITM will fall back to default external proxy (if configured)
            await self.mitm.update_proxy(None)
        
        # Get network info through proxy stack (unified for all proxy types)
        logger.info("Getting network info through proxy stack...")
        net_info = await get_net_info(self.mitm_port)
        if net_info:
            # Display TLS info
            if 'ua' in net_info:
                logger.info(f"TLS Info - UA: {net_info['ua']}")
            if 'ip' in net_info:
                logger.info(f"TLS Info - IP: {net_info['ip']}")
            if 'ja3' in net_info:
                logger.info(f"TLS Info - JA3: {net_info['ja3']}")
            if 'ja4' in net_info:
                logger.info(f"TLS Info - JA4: {net_info['ja4']}")
        else:
            logger.warning("Proxy stack connection failed")
            return {
                "success": False,
                "code": 500,
                "error": "Proxy stack connection failed",
                "data": task,
            }
        
        self.addon.user_agent = task.get("userAgent", None)
        
        try:
            if task["type"] == "Turnstile":
                if not task.get("siteKey", ""):
                    return {
                        "success": False,
                        "code": 500,
                        "response": None,
                        "error": "Field siteKey is not provided.",
                        "data": task,
                    }
                else:
                    self.addon.turnstile_site_key = task.get("siteKey", "")
                self.addon.turnstile_target_host = urlparse(task["url"]).hostname
            elif task["type"] == "RecaptchaInvisible":
                if (not task.get("siteKey", "")) or (not task.get("action", "")):
                    return {
                        "success": False,
                        "code": 500,
                        "response": None,
                        "error": "Field siteKey or action is not provided.",
                        "data": task,
                    }
                else:
                    self.addon.recaptcha_site_key = task.get("siteKey", "")
                    self.addon.recaptcha_action = task.get("action", "")
                self.addon.recaptcha_invisible_target_host = urlparse(task["url"]).hostname
            elif task["type"] == "CloudflareChallenge":
                self.addon.cloudflare_challenge_target_host = urlparse(task["url"]).hostname
            else:
                return {
                    "success": False,
                    "code": 500,
                    "error": f"Unknown task type '{task['type']}'.",
                    "data": task,
                }
            
            if not self.driver.get(task["url"], timeout=timeout):
                return {
                    "success": False,
                    "code": 500,
                    "response": None,
                    "error": "Can not connect to the provided url.",
                    "data": task,
                }
            
            cf_bypasser = CloudflareBypasser(self.driver)
            response = None
            if task["type"] == "Turnstile":
                try_count = 0
                while self.driver:
                    if (datetime.now() - start_time).total_seconds() > timeout:
                        logger.info("Exceeded maximum time. Bypass failed.")
                        response = None
                        error = "Timeout to solve the turnstile, please retry later."
                        break
                    
                    # Check for token first before any other operations
                    token = self.addon.result
                    if token:
                        response = {
                            "token": token
                        }
                        logger.debug("Successfully obtained turnstile token.")
                        break
                        
                    try:
                        if try_count % 5 == 0:
                            logger.debug(f"Attempt {int(try_count / 5 + 1)}: Trying to click turnstile...")
                            cf_bypasser.click_verification_button()
                    except Exception as e:
                        logger.warning(f"Error clicking verification button: {str(e)}")
                        # Don't break here, continue to check for token
                        
                    try_count += 1
                    time.sleep(0.1)
            elif task["type"] == "RecaptchaInvisible":
                while self.driver:
                    if (datetime.now() - start_time).total_seconds() > timeout:
                        logger.info("Exceeded maximum time. Bypass failed.")
                        response = None
                        error = "Timeout to solve the captcha, please retry later."
                        break
                    for _ in range(100):
                        token = self.addon.result
                        if token:
                            break
                        else:
                            time.sleep(0.1)
                    if token:
                        response = {
                            "token": token
                        }
                        break
                    time.sleep(0.5)
            elif task["type"] == "CloudflareChallenge":
                try_count = 0
                bypass_failed_reason = None
                
                while self.driver and (not cf_bypasser.is_bypassed()):
                    if 0 < cf_bypasser.max_retries + 1 <= try_count:
                        logger.info("Exceeded maximum retries. Bypass failed.")
                        bypass_failed_reason = "max_retries"
                        break
                    if (datetime.now() - start_time).total_seconds() > timeout:
                        logger.info("Exceeded maximum time. Bypass failed.")
                        bypass_failed_reason = "timeout"
                        break
                    logger.debug(f"Attempt {try_count + 1}: Verification page detected. Trying to bypass...")
                    cf_bypasser.click_verification_button()
                    try_count += 1
                    time.sleep(0.5)
                
                if cf_bypasser.is_bypassed():
                    logger.debug("Bypass successful.")
                    # After bypass, poll up to 5 seconds for cf_clearance to appear
                    poll_start = datetime.now()
                    cf_clearance = ""
                    while (datetime.now() - poll_start).total_seconds() < 5:
                        cookies = {
                            cookie.get("name", ""): cookie.get("value", "")
                            for cookie in self.driver.cookies()
                        }
                        cf_clearance = cookies.get("cf_clearance", "")
                        if cf_clearance:
                            break
                        time.sleep(0.1)
                else:
                    logger.debug("Bypass failed.")
                    cookies = {
                        cookie.get("name", ""): cookie.get("value", "")
                        for cookie in self.driver.cookies()
                    }
                    cf_clearance = cookies.get("cf_clearance", "")
                if cf_clearance:
                    response = {
                        "cookies": {
                            "cf_clearance": cf_clearance
                        },
                        "headers": {
                            "User-Agent": self.addon.user_agent or self.driver.user_agent
                        }
                    }
                else:
                    response = {}
                
                content = self.driver.html
                print(content)
                print(cookies)
                
                if task.get('content', False):
                    content = self.driver.html
                    if len(content) < 30 * 1024 * 1024:
                        response["content"] = self.driver.html
                
                # Set appropriate error message based on failure reason
                if not response:
                    response = None
                    if bypass_failed_reason == "timeout":
                        elapsed_time = int((datetime.now() - start_time).total_seconds())
                        error = f"Cloudflare bypass failed due to timeout after {elapsed_time} seconds. Consider increasing the timeout value."
                    elif bypass_failed_reason == "max_retries":
                        error = f"Cloudflare bypass failed after {cf_bypasser.max_retries} retries. The challenge may be too complex or network conditions poor."
                    else:
                        error = "No response, may be the url is not protected by cloudflare challenge, please retry later."
            else:
                return {
                    "success": False,
                    "code": 500,
                    "error": f"Unknown task type '{task['type']}'.",
                    "data": task,
                }
            # Stop screencast if it was started
            task_type = task.get("type", "unknown")
            screencast_file = self._stop_screencast(task_type=task_type)
                    
            if response:
                result = {
                    "success": True,
                    "code": 200,
                    "response": response,
                    "data": task,
                }
                if screencast_file:
                    result["screencast_file"] = screencast_file
                return result
            else:
                result = {
                    "success": False,
                    "code": 500,
                    "response": response,
                    "error": error,
                    "data": task,
                }
                if screencast_file:
                    result["screencast_file"] = screencast_file
                return result
    
        finally:
            if linksocks:
                try:
                    links.stop()
                except Exception:
                    pass