import asyncio
from typing import Optional, Dict, Any
import logging

import pproxy

logger = logging.getLogger(__name__)


class DynamicProxy:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """Initialize proxy server

        Args:
            host: Listen address
            port: Listen port
            username: Optional username for authentication
            password: Optional password for authentication
        """
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._server = None
        self._handler = None
        self._upstream_proxies = None  # List[pproxy.Connection]
        self._proxy_strs = None        # List[str]

    @property
    def host(self):
        return self._host

    @property
    def port(self):
        return self._port

    async def set_upstream_proxy(self, proxy_config: Optional[Dict[str, Any]] = None) -> None:
        """Set upstream proxy

        Args:
            proxy_config: Proxy configuration dictionary, format:
            {
                "scheme": "socks5",
                "host": "127.0.0.1",
                "port": 1080,
                "username": "user",  # optional
                "password": "pass"   # optional
            }
            If None, clear proxy configuration
        """
        def _to_conn(cfg: Dict[str, Any]) -> tuple[pproxy.Connection, str]:
            scheme = cfg.get("scheme", "").lower()
            host = cfg.get("host", "")
            port = cfg.get("port", 0)
            username = cfg.get("username")
            password = cfg.get("password")
            if not all([scheme, host, port]):
                raise ValueError("Incomplete proxy configuration")
            uri = f"{scheme}://{host}:{port}"
            if username and password:
                uri += f"#{username}:{password}"
            return pproxy.Connection(uri), uri

        new_proxy_strs = None
        new_upstream_list = None
        if proxy_config:
            if isinstance(proxy_config, list):
                new_upstream_list = []
                new_proxy_strs = []
                for cfg in proxy_config:
                    conn, uri = _to_conn(cfg)
                    new_upstream_list.append(conn)
                    new_proxy_strs.append(uri)
            else:
                conn, uri = _to_conn(proxy_config)
                new_upstream_list = [conn]
                new_proxy_strs = [uri]

        # Only proceed if proxy configuration has changed
        if new_proxy_strs == self._proxy_strs:
            logger.debug(f"Upstream proxy configuration unchanged: {self._proxy_strs}")
            return

        logger.info(f"Upstream proxy chain changed from '{self._proxy_strs}' to '{new_proxy_strs}'")
        self._proxy_strs = new_proxy_strs
        self._upstream_proxies = new_upstream_list

        # If server is already running, restart it to apply the new configuration
        if self._handler:
            logger.info("Restarting dynamic proxy to apply changes.")
            await self.stop()
            await self.start()

    async def start(self) -> None:
        """Start proxy server"""
        server_str = f"http+socks4+socks5://{self._host}:{self._port}"
        if self._username and self._password:
            server_str += f"#{self._username}:{self._password}"

        self._server = pproxy.Server(server_str)

        args = {
            "verbose": logger.info,
            "rserver": [],  # Empty list means direct connection
        }

        if self._upstream_proxies:
            args["rserver"] = self._upstream_proxies

        self._handler = await self._server.start_server(args)
        if self._upstream_proxies:
            logger.info(f"Dynamic proxy started on {self._host}:{self._port} with upstream chain: {self._proxy_strs}")
        else:
            logger.info(f"Dynamic proxy started on {self._host}:{self._port} with direct connection.")

    async def stop(self) -> None:
        """Stop proxy server"""
        if self._handler:
            self._handler.close()
            await self._handler.wait_closed()
            self._handler = None
            self._server = None
            logger.info("Dynamic proxy stopped.")

    async def __aenter__(self) -> "DynamicProxy":
        """Async context manager entry"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.stop()


if __name__ == "__main__":

    async def main():
        async with DynamicProxy(port=8080) as proxy_server:
            await proxy_server.set_upstream_proxy(
                {
                    "scheme": "socks5",
                    "host": "127.0.0.1",
                    "port": 1080,
                }
            )
            await asyncio.Future()

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
