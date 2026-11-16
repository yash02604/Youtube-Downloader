"""Atomic JSON state/history storage and legacy restart recovery.

Mixin host supplies a lock, job mapping, paths and URL/filename validators.
No GUI, framework or network dependencies.
"""
import json
import logging
import os
import time
import uuid
from pathlib import Path
from config import config_manager
from logging_utils import redact_sensitive_text
from .models import Download, DownloadPriority

logger = logging.getLogger(__name__)
HISTORY_LIMIT = 200


class DownloadPersistence:
    @staticmethod
    def _migrate_legacy_json(source: Path, destination: Path, expected_type):
        """Copy a legacy working-directory JSON file once, preserving the original."""
        if destination.exists() or not source.is_file():
            return
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(payload, expected_type):
                return
            DownloadPersistence._write_json_atomic(destination, payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return


    def _load_history(self) -> list:
        if self.history_file.exists():
            try:
                data = json.loads(self.history_file.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
                logger.warning("Download history has an unexpected format: %s", self.history_file)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("History load error (%s): %s", self.history_file, exc)
        return []


    def _save_history(self):
        try:
            with self.lock:
                limit = max(10, min(5000, int(config_manager.get("history_limit", HISTORY_LIMIT))))
                self._write_json_atomic(
                    self.history_file, list(self.history[-limit:])
                )
        except (OSError, TypeError, ValueError) as exc:
            logger.error("History save error: %s", exc)


    @staticmethod
    def _write_json_atomic(path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(
            f"{path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with open(temporary_path, "x", encoding="utf-8") as temporary_file:
                json.dump(data, temporary_file, indent=2, ensure_ascii=False)
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()


    def _persist_downloads(self):
        with self.lock:
            resumable = [
                self._download_to_dict(download)
                for download in self.downloads.values()
                if download.status not in {"Completed", "Cancelled"}
            ]
            try:
                self._write_json_atomic(self.state_file, resumable)
            except (OSError, TypeError, ValueError) as exc:
                logger.error("Download state save error (%s): %s", self.state_file, exc)


    def _load_persisted_downloads(self):
        if not self.state_file.exists():
            return
        try:
            saved_downloads = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(saved_downloads, list):
                raise ValueError("download state must contain a list")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Download state load error (%s): %s", self.state_file, exc)
            return

        for saved in saved_downloads:
            try:
                if not isinstance(saved, dict):
                    raise ValueError("download entry must be an object")
                download_id = saved["id"]
                url = self._validate_url(saved["url"])
                destination_dir = os.path.abspath(saved["destination_dir"])
                download = Download(
                    url,
                    destination_dir,
                    download_id,
                    num_threads=int(saved.get("num_threads", 1)),
                    is_video=bool(saved.get("is_video", False)),
                    custom_filename=saved.get("custom_filename"),
                    priority=int(saved.get("priority", DownloadPriority.DEFAULT)),
                )
                download.download_type = saved.get("download_type", "Video")
                download.video_quality = saved.get("video_quality", "Best")
                download.audio_quality = saved.get("audio_quality", "Best")
                download.playlist_group_id = saved.get("playlist_group_id")
                download.playlist_entry_id = saved.get("playlist_entry_id")
                download.playlist_title = saved.get("playlist_title")
                download.playlist_index = saved.get("playlist_index")
                download.playlist_count = saved.get("playlist_count")
                filename = saved.get("current_filename")
                if self._is_safe_download_filename(filename):
                    download.current_filename = filename
                    download.download_path = os.path.join(destination_dir, filename)
                download.part_path = download.download_path + ".part"
                if os.path.isfile(download.part_path):
                    download.downloaded = os.path.getsize(download.part_path)
                download.file_size = max(0, int(saved.get("file_size", 0) or 0))
                saved_error = saved.get("error_message")
                download.error_message = (
                    redact_sensitive_text(saved_error) if isinstance(saved_error, str) else None
                )
                download.error_category = saved.get("error_category")
                download.etag = saved.get("etag")
                download.last_modified = saved.get("last_modified")
                download.effective_url = saved.get("effective_url")
                download.video_stage = saved.get("video_stage")
                download.execution_generation = max(
                    0, int(saved.get("execution_generation", 0) or 0)
                )
                download.retry_count = max(0, int(saved.get("retry_count", 0) or 0))
                download.video_title = saved.get("video_title")
                download.extractor = saved.get("extractor")
                download.video_id = saved.get("video_id")
                download.completed_at = saved.get("completed_at")
                download.selected_video_format = saved.get("selected_video_format")
                download.selected_audio_format = saved.get("selected_audio_format")
                download.selected_format_summary = saved.get("selected_format_summary")
                download.media_validation_status = saved.get("media_validation_status")
                download.subtitle_languages = list(saved.get("subtitle_languages") or [])
                download.download_subtitles = bool(saved.get("download_subtitles", False))
                download.embed_subtitles = bool(saved.get("embed_subtitles", False))
                download.save_thumbnail = bool(saved.get("save_thumbnail", False))
                download.embed_thumbnail = bool(saved.get("embed_thumbnail", False))
                download.embed_metadata = bool(saved.get("embed_metadata", False))
                download.optional_media_warnings = [
                    redact_sensitive_text(warning[:300])
                    for warning in saved.get("optional_media_warnings", [])
                    if isinstance(warning, str)
                ]
                prior_status = saved.get("status")
                download.status = (
                    "Interrupted" if prior_status in {
                        "Downloading", "Fetching info", "Processing", "Merging",
                        "Converting", "Pausing", "Cancelling", "Analyzing",
                    } else prior_status if prior_status in {"Paused", "Error", "Interrupted"}
                    else "Paused"
                )
                download.start_time = None
                download.last_update_time = time.time()
                self.downloads[download_id] = download
            except (KeyError, TypeError, ValueError, OSError) as exc:
                logger.warning("Skipping invalid recovered download: %s", exc)

        self._persist_downloads()

