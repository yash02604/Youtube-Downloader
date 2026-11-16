import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config import ConfigManager, get_application_dir, youtube_session_options


class ConfigManagerTests(unittest.TestCase):
    def test_browser_session_is_opt_in_and_limited_to_youtube(self):
        with patch("config.config_manager.get", return_value="Off"):
            self.assertEqual(youtube_session_options("https://youtube.com/watch?v=abc"), {})
        with patch("config.config_manager.get", return_value="Edge"):
            self.assertEqual(youtube_session_options("https://www.youtube.com/watch?v=abc"),
                             {"cookiesfrombrowser": ("edge",)})
            self.assertEqual(youtube_session_options("https://youtu.be/abcdefghijk"),
                             {"cookiesfrombrowser": ("edge",)})
            self.assertEqual(youtube_session_options("https://youtube.com.evil.test/"), {})
            self.assertEqual(youtube_session_options("https://example.com/"), {})

    def test_defaults_include_safe_media_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = ConfigManager(Path(temp) / "config.json", migrate_legacy=False)

        self.assertEqual(manager.get("max_concurrent_downloads"), 3)
        self.assertFalse(manager.get("download_subtitles"))
        self.assertFalse(manager.get("embed_thumbnail"))
        self.assertEqual(manager.get("overwrite_behavior"), "rename")
        self.assertEqual(manager.get("default_video_quality"), "Best Available")
        self.assertEqual(manager.get("default_audio_quality"), "Best Original Audio")

    def test_application_directory_uses_executable_directory_when_frozen(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", r"C:\Program Files\Vdm\VideoDownloadManager.exe"
        ):
            self.assertEqual(get_application_dir(), Path(r"C:\Program Files\Vdm"))

    def test_legacy_config_is_migrated_without_deleting_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "old" / "app_config.json"
            legacy.parent.mkdir()
            legacy.write_text(json.dumps({"max_concurrent_downloads": 5}), encoding="utf-8")
            target = root / "user-data" / "app_config.json"
            manager = ConfigManager(target, legacy_file=legacy)

            self.assertEqual(manager.get("max_concurrent_downloads"), 5)
            self.assertTrue(target.exists())
            self.assertTrue(legacy.exists())
            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertFalse(saved["download_subtitles"])

    def test_invalid_values_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "app_config.json"
            target.write_text(json.dumps({
                "max_concurrent_downloads": 999,
                "default_download_path": "",
                "history_limit": -4,
            }), encoding="utf-8")
            manager = ConfigManager(target, migrate_legacy=False)

        self.assertEqual(manager.get("max_concurrent_downloads"), 3)
        self.assertTrue(manager.get("default_download_path"))
        self.assertEqual(manager.get("history_limit"), 200)

    def test_save_is_atomic_and_round_trips_new_values(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "app_config.json"
            manager = ConfigManager(target, migrate_legacy=False)
            manager.set("ffmpeg_location", r"C:\\ffmpeg\\bin")
            manager.save_config()

            loaded = ConfigManager(target, migrate_legacy=False)
            self.assertEqual(loaded.get("ffmpeg_location"), r"C:\\ffmpeg\\bin")
            self.assertEqual(list(Path(temp).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
