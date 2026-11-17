"""Deno discovery and yt-dlp JavaScript runtime configuration."""

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from format_selector import FormatSelector

logger = logging.getLogger(__name__)

MIN_SUPPORTED_DENO = (2, 3, 0)
MIN_SUPPORTED_NODE = (22, 0, 0)
_validated_runtimes = {}


@dataclass(frozen=True)
class DenoRuntime:
    path: str
    version: str

    @property
    def name(self):
        return "deno"

    @property
    def ytdlp_options(self):
        """Return the yt-dlp Python API options for this verified runtime."""
        return {"js_runtimes": {"deno": {"path": self.path}}}


def discover_deno(
    configured_path: Optional[str] = None,
    application_dir: Optional[Path] = None,
    *,
    quiet: bool = False,
) -> Optional[DenoRuntime]:
    """Find a working, supported Deno executable in app, user, or PATH locations."""
    executable_name = "deno.exe" if os.name == "nt" else "deno"
    app_dir = Path(application_dir or Path(__file__).resolve().parent)
    roots = []

    if configured_path:
        configured = Path(os.path.expandvars(os.path.expanduser(configured_path)))
        roots.append(configured if configured.is_dir() else configured.parent)

    bundle_roots = [app_dir]
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        bundle_roots.append(Path(frozen_root))
    bundle_roots.append(Path(sys.executable).resolve().parent)
    for root in bundle_roots:
        roots.extend((root, root / "deno", root / "bin", root / "deno" / "bin"))

    candidates = []
    if configured_path:
        configured = Path(os.path.expandvars(os.path.expanduser(configured_path)))
        if configured.is_file():
            candidates.append(configured)
    candidates.extend(root / executable_name for root in roots)

    # Deno's official Windows installer uses %USERPROFILE%\.deno\bin.
    candidates.append(Path.home() / ".deno" / "bin" / executable_name)
    if os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        candidates.append(local_app_data / "deno" / "bin" / executable_name)

    search_path = os.pathsep.join(filter(None, (
        os.environ.get("PATH", ""),
        FormatSelector._registered_windows_path(),
    )))
    path_deno = shutil.which("deno", path=search_path)
    if path_deno:
        candidates.append(Path(path_deno))

    seen = set()
    for executable in candidates:
        key = os.path.normcase(str(executable))
        if key in seen:
            continue
        seen.add(key)
        if not executable.is_file():
            continue
        try:
            signature = (executable.stat().st_size, executable.stat().st_mtime_ns)
        except OSError:
            continue
        cached = _validated_runtimes.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        try:
            result = subprocess.run(
                [str(executable), "--version"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        match = re.search(
            r"(?im)^\s*deno\s+v?(\d+)\.(\d+)\.(\d+)(?:\s|$)",
            result.stdout or "",
        )
        if not match:
            continue
        version_tuple = tuple(int(part) for part in match.groups())
        version = ".".join(match.groups())
        if version_tuple < MIN_SUPPORTED_DENO:
            if not quiet:
                logger.info(
                    "Ignoring unsupported Deno %s at %s (minimum %s)",
                    version,
                    executable,
                    ".".join(map(str, MIN_SUPPORTED_DENO)),
                )
            continue

        runtime = DenoRuntime(str(executable.resolve()), version)
        _validated_runtimes[key] = (signature, runtime)
        if not quiet:
            logger.info("Resolved Deno JavaScript runtime: %s (%s)", runtime.path, runtime.version)
        return runtime

    if not quiet:
        logger.info("Deno JavaScript runtime was not found or failed validation")
    return None


@dataclass(frozen=True)
class WebJavaScriptRuntime:
    name: str
    path: str
    version: str

    @property
    def ytdlp_options(self):
        return {"js_runtimes": {self.name: {"path": self.path}}}


def discover_web_runtime(
    configured_deno_path: Optional[str] = None,
    application_dir: Optional[Path] = None,
    *,
    configured_node_path: Optional[str] = None,
) -> Optional[WebJavaScriptRuntime]:
    """Select installed Deno >=2.3 or Node >=22 without logging local paths.

    No runtime or solver assets are downloaded. Explicit yt-dlp options keep its
    default Deno configuration from hiding a validated Node fallback.
    """
    deno = discover_deno(configured_deno_path, application_dir, quiet=True)
    if deno:
        return WebJavaScriptRuntime("deno", deno.path, deno.version)

    # The optional official wheel installs an executable outside the service
    # source tree; its own locator also handles pip --target layouts.
    try:
        import deno as deno_package
        packaged_path = deno_package.find_deno_bin()
    except (ImportError, OSError, RuntimeError, AttributeError):
        packaged_path = None
    if packaged_path:
        deno = discover_deno(packaged_path, application_dir, quiet=True)
        if deno:
            return WebJavaScriptRuntime("deno", deno.path, deno.version)

    executable_name = "node.exe" if os.name == "nt" else "node"
    candidates = []
    if configured_node_path:
        configured = Path(os.path.expandvars(os.path.expanduser(configured_node_path)))
        candidates.append(configured / executable_name if configured.is_dir() else configured)
    app_dir = Path(application_dir or Path(__file__).resolve().parent)
    roots = [app_dir, Path(sys.executable).resolve().parent]
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS))
    for root in roots:
        candidates.extend((root / executable_name, root / "bin" / executable_name,
                           root / "node" / executable_name, root / "node" / "bin" / executable_name))
    search_path = os.pathsep.join(filter(None, (
        os.environ.get("PATH", ""), FormatSelector._registered_windows_path(),
    )))
    node_on_path = shutil.which("node", path=search_path)
    if node_on_path:
        candidates.append(Path(node_on_path))
    seen = set()
    for executable in candidates:
        key = os.path.normcase(str(executable))
        if key in seen or not executable.is_file():
            continue
        seen.add(key)
        try:
            result = subprocess.run(
                [str(executable), "--version"], check=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        match = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", result.stdout or "")
        if not match or tuple(map(int, match.groups())) < MIN_SUPPORTED_NODE:
            continue
        return WebJavaScriptRuntime("node", str(executable.resolve()), ".".join(match.groups()))
    return None
