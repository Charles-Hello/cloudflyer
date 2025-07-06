import asyncio
import logging
import time
import threading
import random
import string
from typing import List, Type

from mitmproxy import http, options, ctx
from mitmproxy.tools.dump import DumpMaster

from .proxy import DynamicProxy
from .utils import get_free_port

logger = logging.getLogger(__name__)

class ExampleAddon:
    def __init__(self):
        self.num = 0

    def response(self, flow):
        self.num = self.num + 1
        flow.response.headers["count"] = str(self.num)
        
    def request(self, flow):
        if "abc.com" in flow.request.pretty_host:
            flow.response = http.Response.make(
                200,
                b"Hello!",
                {"Content-Type": "text/html"}
            )

class MITMProxy:
    def __init__(self, host='127.0.0.1', port: int = 8080, username: str =None, password: str = None, certdir: str = "~/.mitmproxy", addons: List[Type] = None):
        self._host = host
        self._mitm_port = port
        self._master = None
        self._certdir = certdir
        self._thread = None
        self._loop: asyncio.BaseEventLoop = None
        self._running = False
        self._addons = addons or []
        # Generate random username and password for dynamic proxy
        random_username = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        random_password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
        random_port = get_free_port()
        self._dynamic_proxy = DynamicProxy(
            host="127.0.0.1", 
            port=random_port, 
            username=random_username,
            password=random_password
        )
        self._upstream_uri = f"http://127.0.0.1:{random_port}"
        self._upstream_auth = f"{random_username}:{random_password}"
        self._username = username
        self._password = password
        
    async def _run_proxy(self):
        logger.info(f"Starting MITM proxy on http://127.0.0.1:{self._mitm_port}.")
        logger.info(f"Starting dynamic proxy on {self._upstream_uri}.")
        await self._dynamic_proxy.start()
        
        opts = options.Options(
            listen_host=self._host,
            listen_port=self._mitm_port,
            ssl_insecure=True,
            confdir=self._certdir,
            mode=[f"upstream:{self._upstream_uri}"],
        )

        self._master = DumpMaster(opts)
        self._master.addons.add(*self._addons)
        ctx.options.flow_detail = 0
        ctx.options.termlog_verbosity = "error"
        ctx.options.upstream_auth = self._upstream_auth
        ctx.options.connection_strategy = "lazy"
        if self._username and self._password:
            ctx.options.proxyauth = f"{self._username}:{self._password}"
        self._running = True
        await self._master.run()

    def start(self):
        """Start proxy in a separate thread"""
        def run_in_thread():
            # On Windows, the default ProactorEventLoop has been known to raise sporadic
            # `OSError: [WinError 64]` errors when a client aborts a connection right
            # after it has been accepted. These errors are harmless for our MITM use
            # case but they bubble up as *unhandled* and may break the proxy loop or
            # flood the logs.  Switching to the classic SelectorEventLoop gets rid of
            # this Windows-specific issue.  We only do this inside the proxy thread so
            # it does not affect the rest of the application.
            if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            # Provide a custom exception handler that silences the spurious WinError 64
            # while keeping the default behaviour for everything else.
            def _ignore_winerror_64(loop, context):
                exc = context.get("exception")
                if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 64:
                    logger.debug("Ignored WinError 64 — the network name is no longer available.")
                    return  # swallow the error silently
                loop.default_exception_handler(context)

            self._loop = asyncio.new_event_loop()
            self._loop.set_exception_handler(_ignore_winerror_64)
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._run_proxy())

        self._thread = threading.Thread(target=run_in_thread)
        self._thread.start()

    def stop(self):
        """Stop the proxy server"""
        if self._running and self._master:
            logger.info("Stopping MITM proxy.")
            self._master.shutdown()
            if self._loop:
                logger.info("Stopping dynamic proxy.")
                self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._dynamic_proxy.stop()))
            if self._thread:
                self._thread.join()
            self._running = False
            logger.info("MITM proxy stopped.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def update_proxy(self, proxy_config=None):
        """Update upstream proxy configuration"""
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: self._dynamic_proxy.set_upstream_proxy(proxy_config))
        else:
            self._dynamic_proxy.set_upstream_proxy(proxy_config)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with MITMProxy(addons=[ExampleAddon()]) as proxy:
        proxy.update_proxy({
            "scheme": "socks5",
            "host": "127.0.0.1",
            "port": 1080,
        })
        time.sleep(60)