import os
import platform
import shutil
import stat
import tempfile
import time
import logging
from pathlib import Path
from typing import Optional
import appdirs
import urllib.request


RELEASE_BASE = {
	"hazetunnel": "https://github.com/zetxtech/hazetunnel/releases/latest/download",
	"linksocks": "https://github.com/linksocks/linksocks/releases/latest/download",
}
RELEASE_BASE_PROXY = {
	"hazetunnel": "https://gh-proxy.com/https://github.com/zetxtech/hazetunnel/releases/latest/download",
	"linksocks": "https://gh-proxy.com/https://github.com/linksocks/linksocks/releases/latest/download",
}


def _detect_asset_names(tool: str) -> list[str]:
	"""Return list of platform-specific asset filenames (include fallbacks)."""
	sysname = platform.system()
	arch = platform.machine().lower()
	def suffix(name: str) -> str:
		if sysname == "Windows":
			return f"{name}-windows-amd64.exe" if ("64" in arch or arch in ("amd64", "x86_64")) else f"{name}-windows-386.exe"
		elif sysname == "Darwin":
			return f"{name}-darwin-arm64" if "arm" in arch else f"{name}-darwin-amd64"
		else:
			return f"{name}-linux-arm64" if ("arm" in arch and arch != "x86_64") else f"{name}-linux-amd64"
	if tool == "hazetunnel":
		return [suffix("hazetunnel")]
	if tool == "linksocks":
		return [suffix("linksocks")]
	raise ValueError("Unknown tool: " + tool)


def _format_bytes(num_bytes: float) -> str:
	units = ["B", "KB", "MB", "GB", "TB"]
	idx = 0
	val = float(num_bytes)
	while val >= 1024 and idx < len(units) - 1:
		val /= 1024.0
		idx += 1
	return f"{val:.1f}{units[idx]}"


def _download_to(url: str, dest: Path, *, label: str) -> bool:
	try:
		logger = logging.getLogger(__name__)
		with urllib.request.urlopen(url, timeout=30) as r:
			length_hdr = r.headers.get("Content-Length")
			total_bytes = int(length_hdr) if length_hdr and length_hdr.isdigit() else None
			chunk_size = 64 * 1024
			read_bytes = 0
			start_ts = time.time()
			last_log = start_ts
			logger.info(f"Starting download {label}: {url}")
			with open(dest, "wb") as f:
				while True:
					chunk = r.read(chunk_size)
					if not chunk:
						break
					f.write(chunk)
					read_bytes += len(chunk)
					now = time.time()
					if now - last_log >= 2:
						elapsed = now - start_ts
						speed = read_bytes / elapsed if elapsed > 0 else 0
						total_str = _format_bytes(total_bytes) if total_bytes else "?"
						logger.info(f"Downloading {label}: {_format_bytes(read_bytes)}/{total_str} ({_format_bytes(speed)}/s)")
						last_log = now
		logger.info(f"Download complete {label}: {_format_bytes(read_bytes)}")
		return True
	except Exception as e:
		logging.getLogger(__name__).debug(f"Download failed {label}: {e}")
		return False


def ensure_tool(tool: str, name_override: Optional[str] = None) -> Optional[Path]:
	"""Ensure the external tool is available locally. Returns executable path or None.

	It tries the following in order:
	1) PATH or current directory
	2) appdirs.user_cache_dir("cloudflyer")/bin/<binary>
	3) Download from GitHub latest release; fallback to gh-proxy mirror
	"""
	bin_name = name_override or (tool + (".exe" if platform.system() == "Windows" else ""))
	# PATH/current
	path = shutil.which(bin_name)
	if path:
		return Path(path)
	if Path(bin_name).exists():
		return Path(bin_name)

	# Use OS-specific user cache directory
	cache_root = Path(appdirs.user_cache_dir("cloudflyer"))
	install_dir = cache_root / "bin"
	install_dir.mkdir(parents=True, exist_ok=True)
	dest = install_dir / bin_name
	if dest.exists():
		return dest

	# Compute assets and URLs
	base = RELEASE_BASE.get(tool)
	base_proxy = RELEASE_BASE_PROXY.get(tool)
	if not base:
		return None
	assets = _detect_asset_names(tool)
	urls = []
	for asset in assets:
		urls.append((f"{base}/{asset}", asset))
		urls.append((f"{base_proxy}/{asset}", asset))

	with tempfile.TemporaryDirectory() as td:
		for u, asset in urls:
			tmp = Path(td) / asset
			label = f"{tool} ({asset})"
			if _download_to(u, tmp, label=label):
				shutil.copy2(tmp, dest)
				# chmod +x on unix
				try:
					mode = os.stat(dest).st_mode
					os.chmod(dest, mode | stat.S_IEXEC)
				except Exception:
					pass
				logging.getLogger(__name__).info(f"Installed {tool} at {dest}")
				return dest

	return None


