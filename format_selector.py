"""yt-dlp format selection and FFmpeg executable discovery."""

import os
import logging
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_validated_ffmpeg_tools = {}
_validated_ffmpeg_tools_lock = threading.Lock()


def _ffmpeg_tools_cache_key(ffmpeg: Path, ffprobe: Path):
    try:
        fingerprints = []
        for executable in (ffmpeg, ffprobe):
            stat = executable.stat()
            fingerprints.append((str(executable.resolve()).casefold(), stat.st_size, stat.st_mtime_ns))
        return tuple(fingerprints)
    except OSError:
        return None


@dataclass(frozen=True)
class FFmpegTools:
    ffmpeg: str
    ffprobe: str

    @property
    def location(self) -> str:
        return str(Path(self.ffmpeg).parent)

    def run_ffprobe(self, arguments, **kwargs):
        """Run ffprobe using the executable resolved with this tool pair."""
        return subprocess.run([self.ffprobe, *arguments], **kwargs)


class FormatSelector:
    """Translate user-facing choices into yt-dlp's format selector syntax."""

    HEIGHTS = {
        "4K (2160p)": 2160,
        "2160p / 4K": 2160,
        "2K (1440p)": 1440,
        "1440p / 2K": 1440,
        "1080p": 1080,
        "720p": 720,
        "480p": 480,
        "360p": 360,
    }

    @staticmethod
    def get_format_string(
        download_type: str,
        video_quality: str,
        audio_quality: str,
        ffmpeg_available: bool = True,
    ) -> str:
        if download_type == "Audio Only":
            if audio_quality.lower() in {"mp3", "mp3 audio", "mp3 high quality"}:
                return "bestaudio/best"
            if audio_quality == "Worst":
                return "worstaudio/worst"
            return "bestaudio"

        if video_quality == "Best MP4 Compatible":
            if ffmpeg_available:
                return "bv[acodec=none][ext=mp4]+ba[ext=m4a]/b[ext=mp4]"
            return "b[ext=mp4]"

        if video_quality == "Worst":
            return "wv[acodec=none]+wa/w" if ffmpeg_available else "w"

        height = FormatSelector.HEIGHTS.get(video_quality)
        if height is None:
            match = re.search(r"(\d+)\s*p", video_quality or "", re.IGNORECASE)
            height = int(match.group(1)) if match else None
        height_filter = f"[height<={height}]" if height else ""

        if ffmpeg_available:
            return f"bv[acodec=none]{height_filter}+ba/b{height_filter}"
        return f"b{height_filter}"

    @staticmethod
    def requires_ffmpeg(download_type: str, audio_quality: str) -> bool:
        return download_type == "Audio Only" and audio_quality.lower() in {
            "mp3", "mp3 audio", "mp3 high quality"
        }

    @staticmethod
    def _registered_windows_path() -> str:
        if os.name != "nt":
            return ""
        try:
            import winreg

            paths = []
            keys = (
                (winreg.HKEY_CURRENT_USER, r"Environment"),
                (
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
                ),
            )
            for hive, subkey in keys:
                try:
                    with winreg.OpenKey(hive, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, "Path")
                except OSError:
                    continue
                if isinstance(value, str):
                    paths.append(os.path.expandvars(value))
            return os.pathsep.join(paths)
        except ImportError:
            return ""

    @staticmethod
    def discover_ffmpeg(
        configured_path: Optional[str] = None,
        application_dir: Optional[Path] = None,
    ) -> Optional[FFmpegTools]:
        """Find working ffmpeg and ffprobe binaries in configured, bundled, or PATH locations."""
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
            roots.extend([
                root,
                root / "ffmpeg",
                root / "bin",
                root / "ffmpeg" / "bin",
            ])

        if os.name == "nt":
            winget_packages = Path(
                os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
            ) / "Microsoft" / "WinGet" / "Packages"
            for package in winget_packages.glob("Gyan.FFmpeg.*"):
                roots.extend(package.glob("*/bin"))

        candidates = []
        if configured_path:
            configured = Path(os.path.expandvars(os.path.expanduser(configured_path)))
            if configured.is_file():
                candidates.append((configured, configured.with_name(
                    "ffprobe.exe" if configured.suffix.lower() == ".exe" else "ffprobe"
                )))

        executable_suffix = ".exe" if os.name == "nt" else ""
        for root in roots:
            candidates.append((
                root / f"ffmpeg{executable_suffix}",
                root / f"ffprobe{executable_suffix}",
            ))

        search_path = os.pathsep.join(filter(None, (
            os.environ.get("PATH", ""),
            FormatSelector._registered_windows_path(),
        )))
        path_ffmpeg = shutil.which("ffmpeg", path=search_path)
        path_ffprobe = shutil.which("ffprobe", path=search_path)
        if path_ffmpeg and path_ffprobe:
            candidates.append((Path(path_ffmpeg), Path(path_ffprobe)))

        seen = set()
        for ffmpeg, ffprobe in candidates:
            key = (str(ffmpeg).lower(), str(ffprobe).lower())
            if key in seen:
                continue
            seen.add(key)
            if (
                ffmpeg.parent.resolve() != ffprobe.parent.resolve()
                or not ffmpeg.is_file()
                or not ffprobe.is_file()
            ):
                continue
            cache_key = _ffmpeg_tools_cache_key(ffmpeg, ffprobe)
            if cache_key is None:
                continue
            with _validated_ffmpeg_tools_lock:
                cached_tools = _validated_ffmpeg_tools.get(cache_key)
            if cached_tools:
                logger.info(
                    "Reusing previously validated FFmpeg/FFprobe executables: %s / %s",
                    cached_tools.ffmpeg, cached_tools.ffprobe,
                )
                return cached_tools
            try:
                subprocess.run(
                    [str(ffmpeg), "-version"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.info(
                    "FFmpeg version check failed for %s: %s: %s",
                    ffmpeg, type(exc).__name__, exc,
                )
                continue
            try:
                subprocess.run(
                    [str(ffprobe), "-version"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                logger.info(
                    "FFprobe version check failed for %s: %s: %s",
                    ffprobe, type(exc).__name__, exc,
                )
                continue
            tools = FFmpegTools(str(ffmpeg.resolve()), str(ffprobe.resolve()))
            with _validated_ffmpeg_tools_lock:
                _validated_ffmpeg_tools[cache_key] = tools
            logger.info("Resolved FFmpeg executable: %s", tools.ffmpeg)
            logger.info("Resolved FFprobe executable: %s", tools.ffprobe)
            return tools
        logger.info("FFmpeg/FFprobe executables were not found or failed validation")
        return None
