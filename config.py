"""
Configuration management system for IDM Clone
Handles user preferences, settings persistence, and default values
"""
import json
import logging
import os
import sys
import uuid
from urllib.parse import urlparse
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def get_user_data_dir(app_name: str = "VideoDownloadManager") -> Path:
    """Return the per-user writable application data directory."""
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / app_name


def get_application_dir() -> Path:
    """Return the directory containing the source entry point or frozen app."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class ConfigManager:
    """Manages application configuration and user preferences"""
    
    def __init__(self, config_file: Optional[Path] = None, *,
                 legacy_file: Optional[Path] = None, migrate_legacy: bool = True):
        self.config_file = Path(config_file) if config_file else get_user_data_dir() / "app_config.json"
        if legacy_file is None and config_file is None and migrate_legacy:
            legacy_file = get_application_dir() / "app_config.json"
        self.legacy_file = Path(legacy_file) if legacy_file else None
        self.defaults = {
            # Download settings
            'default_download_path': str(Path.home() / "Downloads"),
            'max_concurrent_downloads': 3,
            'default_connections': 16,
            'default_video_quality': 'Best Available',
            'default_audio_quality': 'Best Original Audio',
            'default_download_type': 'Video',
            'overwrite_behavior': 'rename',
            'ffmpeg_location': '',
            'deno_location': '',
            'youtube_browser_session': 'Off',
            'download_subtitles': False,
            'subtitle_languages': [],
            'embed_subtitles': False,
            'save_thumbnail': False,
            'embed_thumbnail': False,
            'embed_metadata': False,
            
            # UI settings
            'theme': 'dark',
            'window_width': 1050,
            'window_height': 620,
            'auto_expand_tree': True,
            
            # Advanced settings
            'retry_attempts': 5,
            'retry_delay_base': 2,  # seconds
            'chunk_size': 262144,  # 256KB
            'timeout': 30,
            
            # Browser integration
            'browser_integration_enabled': False,
            'browser_monitor_interval': 1000,  # milliseconds
            
            # RSS settings
            'rss_check_interval': 30,  # minutes
            'rss_feeds': [],
            
            # Performance
            'memory_cleanup_interval': 300,  # seconds
            'history_limit': 200,
            'logging_level': 'INFO',
        }
        self.config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or return defaults"""
        source = self.config_file
        migrating = False
        if not source.exists() and self.legacy_file and self.legacy_file.exists():
            source = self.legacy_file
            migrating = True
        if source.exists():
            try:
                with open(source, 'r', encoding='utf-8') as f:
                    saved_config = json.load(f)
                    if not isinstance(saved_config, dict):
                        raise ValueError("configuration root must be an object")
                    config = self.defaults.copy()
                    config.update(saved_config)
                    config = self._validate_config(config)
                    if migrating:
                        self.config = config
                        self.save_config()
                    return config
            except Exception as e:
                logger.warning("Could not load configuration from %s: %s", source, e)
                return self.defaults.copy()
        return self.defaults.copy()

    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        validated = dict(config)
        integer_ranges = {
            "max_concurrent_downloads": (1, 8),
            "default_connections": (1, 32),
            "history_limit": (10, 5000),
        }
        for key, (minimum, maximum) in integer_ranges.items():
            value = validated.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                validated[key] = self.defaults[key]
        for key in ("default_download_path", "ffmpeg_location", "deno_location"):
            value = validated.get(key)
            if not isinstance(value, str) or (key == "default_download_path" and not value.strip()):
                validated[key] = self.defaults[key]
        if validated.get("youtube_browser_session") not in {"Off", "Brave", "Chrome", "Edge", "Firefox"}:
            validated["youtube_browser_session"] = "Off"
        for key in (
            "download_subtitles", "embed_subtitles", "save_thumbnail",
            "embed_thumbnail", "embed_metadata", "browser_integration_enabled",
            "auto_expand_tree",
        ):
            if not isinstance(validated.get(key), bool):
                validated[key] = self.defaults[key]
        languages = validated.get("subtitle_languages")
        if not isinstance(languages, list) or any(not isinstance(item, str) for item in languages):
            validated["subtitle_languages"] = []
        if validated.get("overwrite_behavior") not in {"rename", "skip", "overwrite"}:
            validated["overwrite_behavior"] = "rename"
        if validated.get("overwrite_behavior") != "rename":
            validated["overwrite_behavior"] = "rename"
        if validated.get("theme") not in {"dark", "light", "system"}:
            validated["theme"] = "dark"
        if validated.get("theme") != "dark":
            validated["theme"] = "dark"
        video_qualities = {
            "Best Available", "Best MP4 Compatible", "2160p / 4K", "1440p / 2K",
            "1080p", "720p", "480p", "360p",
        }
        if validated.get("default_video_quality") not in video_qualities:
            validated["default_video_quality"] = self.defaults["default_video_quality"]
        if validated.get("default_audio_quality") not in {"Best Original Audio", "MP3 High Quality"}:
            validated["default_audio_quality"] = self.defaults["default_audio_quality"]
        if validated.get("default_download_type") not in {"Video", "Audio Only"}:
            validated["default_download_type"] = self.defaults["default_download_type"]
        if validated.get("logging_level") not in {"INFO", "WARNING", "ERROR", "DEBUG"}:
            validated["logging_level"] = "INFO"
        return validated
    
    def save_config(self):
        """Save current configuration to file"""
        try:
            self.config = self._validate_config(self.config)
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.config_file.with_name(f"{self.config_file.name}.{uuid.uuid4().hex}.tmp")
            try:
                with open(temporary, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, indent=2, ensure_ascii=False)
                os.replace(temporary, self.config_file)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except Exception as e:
            logger.error("Could not save configuration to %s: %s", self.config_file, e)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value"""
        fallback = self.defaults.get(key) if default is None else default
        return self.config.get(key, fallback)
    
    def set(self, key: str, value: Any):
        """Set configuration value"""
        self.config[key] = value
    
    def reset_to_defaults(self):
        """Reset all configuration to default values"""
        self.config = self.defaults.copy()
        self.save_config()
    
    def get_download_settings(self) -> Dict[str, Any]:
        """Get all download-related settings"""
        return {
            'download_path': self.get('default_download_path'),
            'max_concurrent': self.get('max_concurrent_downloads'),
            'connections': self.get('default_connections'),
            'video_quality': self.get('default_video_quality'),
            'audio_quality': self.get('default_audio_quality'),
            'download_type': self.get('default_download_type'),
        }
    
    def get_performance_settings(self) -> Dict[str, Any]:
        """Get performance-related settings"""
        return {
            'retry_attempts': self.get('retry_attempts'),
            'retry_delay_base': self.get('retry_delay_base'),
            'chunk_size': self.get('chunk_size'),
            'timeout': self.get('timeout'),
            'memory_cleanup_interval': self.get('memory_cleanup_interval'),
        }


# Global configuration instance
config_manager = ConfigManager()


def youtube_session_options(url: str) -> Dict[str, Any]:
    """Return opt-in browser authentication for YouTube only."""
    browser = config_manager.get("youtube_browser_session", "Off")
    host = (urlparse(url).hostname or "").lower()
    if browser not in {"Brave", "Chrome", "Edge", "Firefox"}:
        return {}
    if host not in {"youtube.com", "youtu.be"} and not host.endswith(".youtube.com"):
        return {}
    return {"cookiesfrombrowser": (browser.lower(),)}
