import logging
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any
import tempfile
import threading

from .downloader import ensure_tool


logger = logging.getLogger(__name__)



class HazeTunnel:
	def __init__(self, addr: str = "127.0.0.1", port: int = 8080, cert_path: Optional[str] = None, key_path: Optional[str] = None, work_dir: Optional[Path] = None, username: Optional[str] = None, password: Optional[str] = None):
		self.addr = addr
		self.port = port
		self.cert_path = cert_path
		self.key_path = key_path
		self.work_dir: Optional[Path] = work_dir
		self.username = username
		self.password = password
		self.process: Optional[subprocess.Popen] = None
		self._upstream_proxy: Optional[str] = None
		self._tempdir: Optional[tempfile.TemporaryDirectory] = None

	@property
	def executable_path(self) -> Optional[Path]:
		# Ensure tool is installed and return path
		return ensure_tool("hazetunnel")

	def set_upstream_proxy(self, proxy_config: Optional[Dict[str, Any]]):
		"""Set upstream proxy string like scheme://host:port or with auth if needed.

		proxy_config format:
		{
		  "scheme": "socks5"|"http",
		  "host": "127.0.0.1",
		  "port": 1080,
		  "username": "u",  # optional
		  "password": "p",  # optional
		}
		"""
		if not proxy_config:
			self._upstream_proxy = None
			return
		scheme = proxy_config.get("scheme", "").lower()
		host = proxy_config.get("host", "")
		port = proxy_config.get("port", 0)
		username = proxy_config.get("username")
		password = proxy_config.get("password")
		if not all([scheme, host, port]):
			raise ValueError("Incomplete proxy configuration")
		proxy_str = f"{scheme}://{host}:{port}"
		if username and password:
			proxy_str = f"{scheme}://{username}:{password}@{host}:{port}"
		self._upstream_proxy = proxy_str

	def start(self, user_agent: Optional[str] = None, payload: Optional[str] = None, verbose: bool = True) -> bool:
		"""Start hazetunnel process. Returns True if started."""
		if self.process and self.process.poll() is None:
			raise RuntimeError("HazeTunnel is already running")
		exe = self.executable_path
		if not exe:
			raise RuntimeError("hazetunnel binary not found and auto-install failed")
		args = [str(exe), "--addr", self.addr, "--port", str(self.port)]
		if self._upstream_proxy:
			args += ["--upstream-proxy", self._upstream_proxy]
		if self.cert_path:
			args += ["-cert", self.cert_path]
		if self.key_path:
			args += ["--key", self.key_path]
		if self.username:
			args += ["--username", self.username]
		if self.password:
			args += ["--password", self.password]
		if user_agent:
			args += ["--user-agent", user_agent]
		if payload:
			args += ["--payload", payload]
		if verbose:
			args += ["--verbose"]

		# Ensure a work dir exists. If not provided, create temp dir and own its lifecycle.
		if not self.work_dir:
			self._tempdir = tempfile.TemporaryDirectory()
			self.work_dir = Path(self._tempdir.name)
		
		# Pipe stdout/stderr and redirect to logging
		self.process = subprocess.Popen(
			args,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			bufsize=1,
			universal_newlines=True,
			cwd=(str(self.work_dir) if self.work_dir else None),
		)

		def _pump(stream, level):
			prefix = "[hazetunnel] "
			for line in iter(stream.readline, ""):
				msg = prefix + line.rstrip() if line else prefix
				logging.log(level, msg)
			stream.close()

		self._stdout_thread = threading.Thread(target=_pump, args=(self.process.stdout, logging.DEBUG), daemon=True)
		self._stderr_thread = threading.Thread(target=_pump, args=(self.process.stderr, logging.DEBUG), daemon=True)
		self._stdout_thread.start()
		self._stderr_thread.start()
		time.sleep(2)
		return self.process.poll() is None

	def stop(self):
		if self.process:
			if self.process.poll() is None:
				self.process.terminate()
				try:
					self.process.wait(timeout=5)
				except subprocess.TimeoutExpired:
					self.process.kill()
			# Join logger threads
			try:
				if hasattr(self, "_stdout_thread") and self._stdout_thread.is_alive():
					self._stdout_thread.join(timeout=1)
				if hasattr(self, "_stderr_thread") and self._stderr_thread.is_alive():
					self._stderr_thread.join(timeout=1)
			except Exception:
				pass
			self.process = None
			# Cleanup tempdir if we created it
			try:
				if self._tempdir is not None:
					self._tempdir.cleanup()
					self._tempdir = None
			except Exception:
				pass


