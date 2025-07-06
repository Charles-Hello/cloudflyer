from datetime import datetime, timedelta
import logging
import os
import socket
from threading import Event
import time
from urllib.parse import urlparse
from importlib import resources
import urllib3

# Disable InsecureRequestWarning
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from cachetools import TTLCache
from mitmproxy.http import HTTPFlow, Response
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
import requests

from .mitm import MITMProxy
from .utils import get_free_port
from .bypasser import CloudflareBypasser
from . import html as html_res

logger = logging.getLogger(__name__)

DEFAULT_ARGUMENTS = [
    "-no-first-run",
    "-force-color-profile=srgb",
    "-metrics-recording-only",
    "-password-store=basic",
    "-use-mock-keychain",
    "-export-tagged-pdf",
    "-no-default-browser-check",
    "-disable-background-mode",
    "-enable-features=NetworkService,NetworkServiceInProcess,LoadCryptoTokenExtension,PermuteTLSExtensions",
    "-disable-features=FlashDeprecationWarning,EnablePasswordsAccountStorage",
    "-deny-permission-prompts",
    "-disable-gpu",
    "-accept-lang=en-US",
    "--window-size=512,512",
    "--disable-infobars",
    "--window-name=Cloudflyer",
    "--disable-sync",
    "--app=https://internals.cloudflyer.com/index",
    "--lang=en",
]

DEFAULT_BROWSER_PATH = os.getenv("CHROME_PATH", None)
DEFAULT_CERT_PATH = os.getenv("CERT_PATH", "~/.mitmproxy")

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
        
    def http_connect(self, flow: HTTPFlow):
        # Use better IP for chinese users
        host = flow.request.host
        if host == "challenges.cloudflare.com":
            ip = socket.gethostbyname("challenges.cf.cname.vvhan.com")
            port = flow.request.port
            flow.request.host = ip
            flow.request.authority = f"{ip}:{port}"
            flow.server_conn.sni = host
            logger.debug(f"CONNECT IP override {host} -> {ip}")
    
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
        # Use better IP for chinese users
        host = flow.request.host
        if host == "challenges.cloudflare.com":
            ip = socket.gethostbyname("challenges.cf.cname.vvhan.com")
            flow.request.host = ip
            # HTTP/1.1 host header keeps original host
            flow.request.headers["Host"] = host
            # HTTP/2 uses :authority
            flow.request.authority = host
            logger.debug(f"HTTP IP override {host} -> {ip}")
        
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
            # Block large responses (>30MB)
            content_length = int(flow.response.headers.get("Content-Length", "0"))
            if content_length > 30 * 1024 * 1024:  # 30MB in bytes
                flow.response = Response.make(
                    404,
                    b"CloudFlyer blocked large file",
                    {"Content-Type": "text/plain"}
                )
                return

            # Block medias
            content_type = flow.response.headers.get("Content-Type", "")
            blocked_types = [
                "image/", "font/", "text/css",
                "audio/", "video/", "application/font",
            ]
            if any(btype in content_type.lower() for btype in blocked_types):
                flow.response = Response.make(
                    404,
                    b"CloudFlyer blocked media",
                    {"Content-Type": "text/plain"}
                )
        
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
        arguments: list = DEFAULT_ARGUMENTS,
        browser_path: str = DEFAULT_BROWSER_PATH,
        certdir: str = DEFAULT_CERT_PATH,
    ):
        self.browser_path = browser_path
        self.certdir = certdir
        self.arguments = arguments
        self.driver: ChromiumPage = None
        self.addon = MITMAddon()
        self.mitm_port = get_free_port()
        self.mitm = MITMProxy(
            port=self.mitm_port,
            certdir=self.certdir,
            addons=[self.addon],
        )
        
    def start(self):
        # Initialize driver with MITM proxy
        self.mitm.start()
        if not self.addon.ready.wait(timeout=10):
            raise RuntimeError("MITM proxy failed to start.")
        options = ChromiumOptions().auto_port()
        options.set_paths(browser_path=self.browser_path)
        options.ignore_certificate_errors(True)
        for argument in self.arguments:
            options.set_argument(argument)
        options.set_proxy(f"http://127.0.0.1:{self.mitm_port}")
        options.set_retry(20, 0.5)
        self.driver = ChromiumPage(addr_or_opts=options, timeout=0.5)
        logger.debug("ChromiumPage driver initialized with MITM proxy.")
        self.driver.get('https://internals.cloudflyer.com/index')

    def stop(self):
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
            return self._task_main(task, timeout=timeout)
        except Exception as e:
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

    def _task_main(self, task: dict, timeout: float):
        start_time = datetime.now()
        self.addon.reset()
        
        # Ensure URL starts with http:// or https://
        if not task["url"].startswith(("http://", "https://")):
            task["url"] = "http://" + task["url"]

        proxy = task.get("proxy")
        wssocks = None
        if proxy and isinstance(proxy, dict):
            self.mitm.update_proxy(proxy)
        else:
            wssocks = task.get("wssocks")
            if wssocks and isinstance(wssocks, dict):
                wssocks_url = wssocks.get("url", None)
                wssocks_token = wssocks.get("token", None)
                if wssocks_url and wssocks_token:
                    from .wssocks import WSSocks
                    
                    wssocks = WSSocks()
                    port = get_free_port()
                    if not wssocks.start(wssocks_token, wssocks_url, port):
                        return {
                            "success": False,
                            "code": 500,
                            "response": None,
                            "error": "Fail to connect to the wssocks proxy.",
                            "data": task,
                        }
                    self.mitm.update_proxy({"scheme": "socks5", "host": "127.0.0.1", "port": port})
                else:
                    return {
                        "success": False,
                        "code": 500,
                        "response": None,
                        "error": "Either wssocks.url or wssocks.token is not provided.",
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
                    time.sleep(2)
            elif task["type"] == "CloudflareChallenge":
                try_count = 0
                while self.driver and (not cf_bypasser.is_bypassed()):
                    if 0 < cf_bypasser.max_retries + 1 <= try_count:
                        logger.info("Exceeded maximum retries. Bypass failed.")
                        break
                    if (datetime.now() - start_time).total_seconds() > timeout:
                        logger.info("Exceeded maximum time. Bypass failed.")
                        break
                    logger.debug(f"Attempt {try_count + 1}: Verification page detected. Trying to bypass...")
                    cf_bypasser.click_verification_button()
                    try_count += 1
                    time.sleep(2)
                if cf_bypasser.is_bypassed():
                    logger.debug("Bypass successful.")
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
                if task.get('content', False):
                    content = self.driver.html
                    if len(content) < 30 * 1024 * 1024:
                        response["content"] = self.driver.html
                if not response:
                    response = None
                    error = "No response, may be the url is not protected by cloudflare challenge, please retry later."
            else:
                return {
                    "success": False,
                    "code": 500,
                    "error": f"Unknown task type '{task['type']}'.",
                    "data": task,
                }
            if response:
                return {
                    "success": True,
                    "code": 200,
                    "response": response,
                    "data": task,
                }
            else:
                return {
                    "success": False,
                    "code": 500,
                    "response": response,
                    "error": error,
                    "data": task,
                }
    
        finally:
            if wssocks:
                wssocks.stop()