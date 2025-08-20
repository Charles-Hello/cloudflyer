import asyncio
import logging
import time
import threading
import random
import string
import os
from typing import List, Type

from mitmproxy import http, options, ctx
from mitmproxy.tools.dump import DumpMaster

from .proxy import DynamicProxy
from .hazetunnel import HazeTunnel
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
            flow.response = http.Response.make(200, b"Hello!", {"Content-Type": "text/html"})


class MITMProxy:
    def __init__(
        self,
        host="127.0.0.1",
        port: int = 8080,
        username: str = None,
        password: str = None,
        certdir: str = "~/.mitmproxy",
        addons: List[Type] = None,
        use_hazetunnel: bool = True,
    ):
        self._host = host
        self._mitm_port = port
        self._master = None
        self._certdir = certdir
        self.thread = None
        self._loop: asyncio.BaseEventLoop = None
        self._running = False
        self._addons = addons or []
        self._use_hazetunnel = use_hazetunnel
        # Generate random username and password for dynamic proxy
        random_username = "".join(random.choices(string.ascii_letters + string.digits, k=8))
        random_password = "".join(random.choices(string.ascii_letters + string.digits, k=12))
        random_port = get_free_port()
        self._dynamic_proxy = DynamicProxy(
            host="127.0.0.1", port=random_port, username=random_username, password=random_password
        )
        
        if self._use_hazetunnel:
            # Generate random username and password for hazetunnel
            self._hazetunnel_username = "".join(random.choices(string.ascii_letters + string.digits, k=8))
            self._hazetunnel_password = "".join(random.choices(string.ascii_letters + string.digits, k=12))
            self._hazetunnel_port = get_free_port()
            self._upstream_uri = f"http://127.0.0.1:{self._hazetunnel_port}"
            self._upstream_auth = f"{self._hazetunnel_username}:{self._hazetunnel_password}"
            self._hazetunnel: HazeTunnel = None
        else:
            # Direct connection to dynamic proxy
            self._hazetunnel_username = None
            self._hazetunnel_password = None
            self._hazetunnel_port = None
            self._upstream_uri = f"http://127.0.0.1:{self._dynamic_proxy.port}"
            self._upstream_auth = f"{self._dynamic_proxy._username}:{self._dynamic_proxy._password}"
            self._hazetunnel: HazeTunnel = None
        
        self._username = username
        self._password = password

    async def _run_proxy(self):
        logger.info(f"Starting MITM proxy on http://127.0.0.1:{self._mitm_port}.")
        logger.info(f"Starting dynamic proxy on socks5h://127.0.0.1:{self._dynamic_proxy.port} and http://127.0.0.1:{self._dynamic_proxy.port}.")
        await self._dynamic_proxy.start()
        
        if self._use_hazetunnel:
            # Start hazetunnel and point it upstream to dynamic proxy (no cert/key). It creates its own temp working dir.
            self._hazetunnel = HazeTunnel(
                addr="127.0.0.1",
                port=self._hazetunnel_port,
                username=self._hazetunnel_username,
                password=self._hazetunnel_password,
            )
            self._hazetunnel.set_upstream_proxy(
                {
                    "scheme": "socks5h",
                    "host": "127.0.0.1",
                    "port": self._dynamic_proxy.port,
                    "username": self._dynamic_proxy._username,
                    "password": self._dynamic_proxy._password,
                }
            )
            # Do not pass cert/key; hazetunnel will generate within its work dir
            logger.info(
                f"Starting hazetunnel on http://127.0.0.1:{self._hazetunnel_port}."
            )
            if not self._hazetunnel.start():
                raise RuntimeError("Failed to start hazetunnel")
        else:
            logger.info("Skipping hazetunnel, using direct connection to pproxy upstream.")

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
        # Set upstream auth to hazetunnel
        if self._upstream_auth:
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

        self.thread = threading.Thread(target=run_in_thread, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the proxy server"""
        if self._running and self._master:
            logger.info("Stopping MITM proxy.")
            self._master.shutdown()
            if self._loop:
                logger.info("Stopping dynamic proxy.")
                self._loop.call_soon_threadsafe(lambda: asyncio.create_task(self._dynamic_proxy.stop()))
            if self._use_hazetunnel and self._hazetunnel:
                logger.info("Stopping hazetunnel.")
                self._hazetunnel.stop()
            if self.thread:
                self.thread.join()
            self._running = False
            logger.info("MITM proxy stopped.")

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    async def update_proxy(self, proxy_config=None):
        """Update upstream proxy configuration"""
        if self._loop:
            future = asyncio.run_coroutine_threadsafe(self._dynamic_proxy.set_upstream_proxy(proxy_config), self._loop)
            future.result()
        else:
            await self._dynamic_proxy.set_upstream_proxy(proxy_config)