"""Video URL detection and metadata extraction using yt-dlp."""

import re
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

import yt_dlp
from config import config_manager, youtube_session_options
from js_runtime import discover_deno


class VideoAnalyzer:
    """Detect supported video URLs and inspect media without downloading it."""

    VIDEO_PATTERNS = {
        "youtube": [r"(?:youtube\.com|youtu\.be)", r"(?:watch\?v=|/)([a-zA-Z0-9_-]{11})"],
        "vimeo": [r"vimeo\.com", r"/(\d+)"],
        "dailymotion": [r"dailymotion\.com", r"/video/([a-zA-Z0-9]+)"],
        "facebook": [r"facebook\.com", r"/watch/?\?v=(\d+)"],
        "instagram": [r"instagram\.com", r"/p/([a-zA-Z0-9_-]+)"],
        "tiktok": [r"tiktok\.com", r"/.*/video/(\d+)"],
    }

    @staticmethod
    def detect_platform(url: str) -> Optional[str]:
        """Return the matching supported platform, if any."""
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None

        domains = {
            "youtube": ("youtube.com", "youtu.be"),
            "vimeo": ("vimeo.com",),
            "dailymotion": ("dailymotion.com", "dai.ly"),
            "facebook": ("facebook.com", "fb.watch"),
            "instagram": ("instagram.com",),
            "tiktok": ("tiktok.com",),
        }
        for platform, suffixes in domains.items():
            if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
                return platform
        return None

    @staticmethod
    def is_video_url(url: str) -> bool:
        """Check supported URL hosts without accepting lookalike domains."""
        return VideoAnalyzer.detect_platform(url) is not None

    @staticmethod
    def is_playlist_url(url: str) -> bool:
        """Detect explicit YouTube playlist URLs."""
        if VideoAnalyzer.detect_platform(url) != "youtube":
            return False
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        query = parse_qs(parsed.query)
        return (
            parsed.path.rstrip("/") == "/playlist"
            or (bool(query.get("list")) and not query.get("v"))
        )

    @staticmethod
    def _normalize_entry(entry: Dict) -> Dict:
        format_fields = (
            "format_id", "format", "url", "width", "height", "fps", "vcodec",
            "acodec", "tbr", "vbr", "abr", "ext", "dynamic_range", "filesize",
            "filesize_approx",
        )
        formats = [
            {key: item.get(key) for key in format_fields if key in item}
            for item in entry.get("formats") or []
        ]
        best_format = max(
            formats,
            key=lambda item: (
                item.get("height") or 0,
                item.get("tbr") or 0,
            ),
            default={},
        )
        heights = sorted({
            int(item["height"]) for item in formats
            if item.get("height") and str(item["height"]).isdigit()
        }, reverse=True)
        return {
            "id": entry.get("id"),
            "title": entry.get("title") or entry.get("id") or "Untitled video",
            "url": entry.get("webpage_url") or entry.get("original_url") or entry.get("url"),
            "duration": entry.get("duration"),
            "channel": entry.get("channel") or entry.get("uploader"),
            "uploader": entry.get("uploader"),
            "thumbnail": entry.get("thumbnail"),
            "formats": formats,
            "resolutions": heights,
            "fps": entry.get("fps") or best_format.get("fps"),
            "video_codec": entry.get("vcodec") or best_format.get("vcodec"),
            "audio_codec": entry.get("acodec") or best_format.get("acodec"),
            "format": entry.get("ext") or best_format.get("ext"),
            "filesize": (
                entry.get("filesize")
                or entry.get("filesize_approx")
                or best_format.get("filesize")
                or best_format.get("filesize_approx")
            ),
            "subtitles": sorted((entry.get("subtitles") or {}).keys()),
            "automatic_subtitles": sorted((entry.get("automatic_captions") or {}).keys()),
        }

    @staticmethod
    def get_video_info(url: str) -> Optional[Dict]:
        """Extract metadata only; media is not downloaded."""
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": "in_playlist",
            "noplaylist": not VideoAnalyzer.is_playlist_url(url),
        }
        options.update(youtube_session_options(url))
        deno_runtime = discover_deno(
            configured_path=config_manager.get("deno_location", ""),
        )
        if deno_runtime:
            options.update(deno_runtime.ytdlp_options)
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            return None

        normalized = VideoAnalyzer._normalize_entry(info)
        entries = info.get("entries")
        if entries is not None:
            normalized["is_playlist"] = True
            normalized["playlist_title"] = info.get("title") or info.get("playlist_title")
            normalized["playlist_count"] = info.get("playlist_count") or info.get("n_entries")
            normalized["entries"] = [
                VideoAnalyzer._normalize_entry(entry)
                for entry in entries if entry
            ]
        else:
            normalized["is_playlist"] = False
        normalized["duration_string"] = info.get("duration_string")
        normalized["extractor"] = info.get("extractor")
        return normalized

    @staticmethod
    def get_download_url(url: str, quality: str = "Best") -> Optional[str]:
        """Return the URL of a directly available format, when one exists."""
        info = VideoAnalyzer.get_video_info(url)
        if not info or info.get("is_playlist"):
            return None
        formats = info.get("formats") or []
        candidates = [
            item for item in formats
            if item.get("url") and item.get("vcodec") != "none"
        ]
        if not candidates:
            return None
        height_match = re.search(r"(\d+)p", quality or "")
        if height_match:
            max_height = int(height_match.group(1))
            candidates = [
                item for item in candidates
                if item.get("height") and item["height"] <= max_height
            ] or candidates
        return max(
            candidates,
            key=lambda item: (item.get("height") or 0, item.get("tbr") or 0),
        ).get("url")
