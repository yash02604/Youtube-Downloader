"""Download domain state and separate runtime execution controls."""
import os
import threading
import json
from urllib.parse import urlparse
from typing import Optional

class DownloadPriority:
    """Enumeration for download priorities"""
    HIGH = 3
    MEDIUM = 2
    LOW = 1
    DEFAULT = 2


class DownloadJob:
    """Represents a single download task"""

    def to_dict(self):
        return download_to_dict(self)

    @classmethod
    def from_dict(cls, data):
        """Read the legacy domain schema, without starting or restoring workers.

        Runtime recovery additionally validates URLs, paths and partial files in
        the persistence layer. This method does not authorize filesystem access.
        """
        if not isinstance(data, dict):
            raise ValueError('download entry must be an object')
        for key in ('id', 'url', 'destination_dir'):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError(f'{key} must be nonempty text')
        json.dumps(data, allow_nan=False)
        job = cls(data['url'], data['destination_dir'], data['id'])
        fields = download_to_dict(job)
        for key in fields:
            if key in data and key not in {'id', 'url', 'destination_dir', 'previous_state'}:
                setattr(job, key, json.loads(json.dumps(data[key], allow_nan=False)))
        return job
    
    def __init__(self, url: str, download_path: str, download_id: str, 
                 num_threads: int = 8, is_video: bool = False, video_info: Optional[dict] = None,
                 custom_filename: Optional[str] = None, priority: int = DownloadPriority.DEFAULT):
        self.original_url = url  # Keep original URL for video downloads
        self.url = url
        self.destination_dir = os.path.abspath(download_path)
        self.download_path = self.destination_dir  # Will be replaced with actual file path later
        self.download_id = download_id
        self.num_threads = num_threads
        self.is_video = is_video
        self.video_info = video_info
        self.download_type = "Video"  # "Video" or "Audio Only"
        self.video_quality = "Best"  # Quality selection
        self.audio_quality = "Best"  # Audio quality selection
        self.custom_filename = custom_filename
        self.priority = priority  # Download priority level
        self.current_filename: Optional[str] = None
        self.pause_requested = False
        self.cancel_requested = False
        self.file_size = 0
        self.downloaded = 0
        self.status = "Queued"  # Queued, Downloading, Paused, Completed, Error, Cancelled, Analyzing
        self.speed = 0  # bytes per second
        self.eta = 0  # estimated time in seconds
        self.error_message = None
        self.error_category = None
        self.temp_files = []
        self.start_time = None
        self.last_update_time = None
        self.last_downloaded = 0
        self.avg_speed = 0
        self.etag = None
        self.last_modified = None
        self.effective_url = None
        self.part_path: Optional[str] = None
        self.video_stage = None
        self.fragment_index = None
        self.fragment_count = None
        self.playlist_group_id = None
        self.playlist_entry_id = None
        self.playlist_title = None
        self.playlist_index = None
        self.playlist_count = None
        self.selected_video_format = None
        self.selected_audio_format = None
        self.selected_format_summary = None
        self.execution_generation = 0
        self.retry_count = 0
        self.video_title: Optional[str] = None
        self.extractor: Optional[str] = None
        self.video_id: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.final_output_path: Optional[str] = None
        self.media_validation_status: Optional[str] = None
        self.subtitle_languages: list[str] = []
        self.download_subtitles = False
        self.embed_subtitles = False
        self.save_thumbnail = False
        self.embed_thumbnail = False
        self.embed_metadata = False
        self.optional_media_warnings: list[str] = []
        
    def _sanitize_name(self, name: str) -> str:
        sanitized = "".join(
            char for char in name if char.isalnum() or char in (" ", "-", "_")
        ).strip(" .")[:100]
        if not sanitized:
            sanitized = "download"
        if sanitized.upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }:
            sanitized = f"_{sanitized}"
        return sanitized
    
    def get_filename(self) -> str:
        """Extract filename from custom input, video info, or URL"""
        default_ext = 'bin'
        if self.download_type == "Audio Only":
            default_ext = 'mp3'
        elif self.is_video and self.video_info:
            # For videos, always use mkv format
            default_ext = 'mkv'
        elif self.is_video:
            default_ext = 'mkv'
        default_ext = default_ext.lstrip('.')
        
        if self.custom_filename:
            name = self.custom_filename.strip()
            base, ext = os.path.splitext(name)
            base = self._sanitize_name(base or name)
            extension = "".join(char for char in ext.lstrip(".") if char.isalnum())[:10]
            return f"{base}.{extension or default_ext}"
        
        if self.is_video and self.video_info:
            title = self.video_info.get('title', 'video')
            base = self._sanitize_name(title)
            return f"{base}.{default_ext}"
        
        parsed = urlparse(self.url)
        filename = os.path.basename(parsed.path)
        base, ext = os.path.splitext(filename)
        base = self._sanitize_name(base or "download")
        extension = "".join(char for char in ext.lstrip(".") if char.isalnum())[:10]
        return f"{base}.{extension or default_ext or 'bin'}"


class Download(DownloadJob):
    """Runtime job retaining the legacy engine interface; never serialize __dict__."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pause_event = threading.Event()
        self.cancel_event = threading.Event()
        self.threads = []
        self.ydl_process = None
        self.worker: Optional[threading.Thread] = None
        self.last_persisted_bytes = 0
        self.slot_active = False
        self.resume_pending = False
        self.active_postprocessor: Optional[str] = None
        self.slot_generation: Optional[int] = None


def download_to_dict(download: DownloadJob) -> dict:
    return {
        "id": download.download_id,
        "url": download.original_url,
        "destination_dir": download.destination_dir,
        "download_path": download.download_path,
        "current_filename": download.current_filename,
        "custom_filename": download.custom_filename,
        "num_threads": download.num_threads,
        "is_video": download.is_video,
        "download_type": download.download_type,
        "video_quality": download.video_quality,
        "audio_quality": download.audio_quality,
        "playlist_group_id": download.playlist_group_id,
        "playlist_entry_id": download.playlist_entry_id,
        "playlist_title": download.playlist_title,
        "playlist_index": download.playlist_index,
        "playlist_count": download.playlist_count,
        "priority": download.priority,
        "file_size": download.file_size,
        "downloaded": download.downloaded,
        "status": download.status,
        "error_message": download.error_message,
        "error_category": download.error_category,
        "etag": download.etag,
        "last_modified": download.last_modified,
        "effective_url": download.effective_url,
        "video_stage": download.video_stage,
        "execution_generation": download.execution_generation,
        "retry_count": download.retry_count,
        "video_title": download.video_title,
        "extractor": download.extractor,
        "video_id": download.video_id,
        "completed_at": download.completed_at,
        "selected_video_format": download.selected_video_format,
        "selected_audio_format": download.selected_audio_format,
        "selected_format_summary": download.selected_format_summary,
        "media_validation_status": download.media_validation_status,
        "subtitle_languages": list(download.subtitle_languages),
        "download_subtitles": download.download_subtitles,
        "embed_subtitles": download.embed_subtitles,
        "save_thumbnail": download.save_thumbnail,
        "embed_thumbnail": download.embed_thumbnail,
        "embed_metadata": download.embed_metadata,
        "optional_media_warnings": list(download.optional_media_warnings),
        "previous_state": download.status,
    }

