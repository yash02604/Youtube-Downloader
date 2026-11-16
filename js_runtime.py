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
_validated_runtimes = {}


@dataclass(frozen=True)
class DenoRuntime:
    path: str
    version: str

    @property
    def ytdlp_options(self):
        """Return the yt-dlp Python API options for this verified runtime."""
        return {"js_runtimes": {"deno": {"path": self.path}}}


def discover_deno(
    configured_path: Optional[str] = None,
    application_dir: Optional[Path] = None,
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
            logger.info(
                "Ignoring unsupported Deno %s at %s (minimum %s)",
                version,
                executable,
                ".".join(map(str, MIN_SUPPORTED_DENO)),
            )
            continue

        runtime = DenoRuntime(str(executable.resolve()), version)
        _validated_runtimes[key] = (signature, runtime)
        logger.info("Resolved Deno JavaScript runtime: %s (%s)", runtime.path, runtime.version)
        return runtime

    logger.info("Deno JavaScript runtime was not found or failed validation")
    return None
