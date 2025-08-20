import asyncio
import shlex
import subprocess
from pathlib import Path
import time
import platform
from typing import Optional
import os
import threading

from .utils import get_free_port
from .downloader import ensure_tool


class LinkSocks:
	def __init__(self):
		self.system = platform.system()
		self.process: Optional[subprocess.Popen] = None

	@property
	def executable_path(self) -> Optional[Path]:
		"""Resolve executable via ensure_tool in user cache dir and PATH."""
		return ensure_tool("linksocks")

	def execute(self, *args) -> subprocess.Popen:
		"""Execute linksocks with given arguments and return Popen object"""
		if not self.executable_path:
			raise RuntimeError(f'{self.executable_path} not found in current dir or PATH')
		# Pipe stdout/stderr to logging
		proc = subprocess.Popen(
			[str(self.executable_path), *args],
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			bufsize=1,
			universal_newlines=True,
		)
		import logging
		logger = logging.getLogger(__name__)
		def _pump(stream, level):
			prefix = "[linksocks] "
			for line in iter(stream.readline, ""):
				msg = prefix + line.rstrip() if line else prefix
				logger.log(level, msg)
			stream.close()
		self._stdout_thread = threading.Thread(target=_pump, args=(proc.stdout, logging.DEBUG), daemon=True)
		self._stderr_thread = threading.Thread(target=_pump, args=(proc.stderr, logging.DEBUG), daemon=True)
		self._stdout_thread.start()
		self._stderr_thread.start()
		return proc

	def start(self, token: str, url: str, port: int = None, threads: int = 1, verbose: bool = True) -> Optional[bool]:
		"""Start linksocks connector

		Args:
			token: Authentication token
			url: Server URL
			port: Local port to listen on
			threads: Worker threads

		Returns:
			True if started, False if failed
		"""
		if self.process and self.process.poll() is None:
			raise RuntimeError("LinkSocks is already running")

		if port is None:
			port = get_free_port()

		args = [
			"client",
			"-t", token,
			"-u", url,
			"-T", str(threads),
			"-p", str(port),
		]
		if verbose:
			args += ["-d"]

		self.process = self.execute(*args)

		time.sleep(3)

		if self.process.poll() is not None:
			return False
		return True

	def stop(self) -> None:
		"""Stop linksocks client if running"""
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


