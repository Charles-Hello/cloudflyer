import os
import platform
import shutil
import stat
import tempfile
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


def _download_to(url: str, dest: Path) -> bool:
	try:
		with urllib.request.urlopen(url, timeout=30) as r, open(dest, "wb") as f:
			shutil.copyfileobj(r, f)
		return True
	except Exception:
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
		urls.append(f"{base}/{asset}")
		urls.append(f"{base_proxy}/{asset}")

	with tempfile.TemporaryDirectory() as td:
		tmp = Path(td) / asset
		for u in urls:
			if _download_to(u, tmp):
				shutil.copy2(tmp, dest)
				# chmod +x on unix
				try:
					mode = os.stat(dest).st_mode
					os.chmod(dest, mode | stat.S_IEXEC)
				except Exception:
					pass
				return dest

	return None


