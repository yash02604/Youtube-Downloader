import tempfile
import os
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

from download_manager import Download, DownloadManager
from format_selector import FFmpegTools, FormatSelector
from js_runtime import DenoRuntime
from media_validation import MediaValidationResult
from video_analyzer import VideoAnalyzer
from yt_dlp.utils import DownloadCancelled
import yt_dlp


class VideoAnalyzerTests(unittest.TestCase):
    def test_supported_hosts_are_parsed_not_matched_as_substrings(self):
        self.assertTrue(VideoAnalyzer.is_video_url("https://www.youtube.com/watch?v=abc"))
        self.assertTrue(VideoAnalyzer.is_video_url("https://youtu.be/abc"))
        self.assertFalse(VideoAnalyzer.is_video_url("https://youtube.com.example.org/watch?v=abc"))

    def test_playlist_detection_does_not_capture_video_links_with_list_parameter(self):
        self.assertTrue(VideoAnalyzer.is_playlist_url("https://www.youtube.com/playlist?list=PL123"))
        self.assertFalse(
            VideoAnalyzer.is_playlist_url(
                "https://www.youtube.com/watch?v=abcdefghijk&list=PL123"
            )
        )

    def test_metadata_is_extracted_without_downloading(self):
        info = {
            "id": "abcdefghijk",
            "title": "Sample",
            "uploader": "Uploader",
            "formats": [
                {"height": 720, "vcodec": "avc1", "acodec": "none"},
                {"height": 1080, "vcodec": "avc1", "acodec": "none"},
            ],
            "subtitles": {"en": [{}]},
            "automatic_captions": {"fr": [{}]},
        }
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = info
        runtime = DenoRuntime("C:\\tools\\deno.exe", "2.3.0")
        with patch("video_analyzer.discover_deno", return_value=runtime):
            with patch("video_analyzer.youtube_session_options", return_value={"cookiesfrombrowser": ("brave",)}), patch("video_analyzer.yt_dlp.YoutubeDL", return_value=ydl) as factory:
                result = VideoAnalyzer.get_video_info(
                    "https://www.youtube.com/watch?v=abcdefghijk"
                )
        self.assertTrue(factory.call_args.args[0]["noplaylist"])
        self.assertEqual(factory.call_args.args[0]["cookiesfrombrowser"], ("brave",))
        self.assertEqual(
            factory.call_args.args[0]["js_runtimes"],
            {"deno": {"path": "C:\\tools\\deno.exe"}},
        )
        self.assertNotIn("remote_components", factory.call_args.args[0])
        ydl.extract_info.assert_called_once_with(
            "https://www.youtube.com/watch?v=abcdefghijk", download=False
        )
        self.assertEqual(result["resolutions"], [1080, 720])
        self.assertEqual(result["subtitles"], ["en"])
        self.assertEqual(result["automatic_subtitles"], ["fr"])

    def test_metadata_omits_js_runtime_configuration_when_deno_is_missing(self):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = {"id": "abcdefghijk", "formats": []}
        with patch("video_analyzer.discover_deno", return_value=None):
            with patch("video_analyzer.yt_dlp.YoutubeDL", return_value=ydl) as factory:
                VideoAnalyzer.get_video_info(
                    "https://www.youtube.com/watch?v=abcdefghijk"
                )
        self.assertNotIn("js_runtimes", factory.call_args.args[0])

    def test_playlist_metadata_keeps_entries_and_does_not_download(self):
        info = {
            "title": "Playlist",
            "playlist_count": 1,
            "entries": [{"id": "abcdefghijk", "title": "One"}],
        }
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = info
        with patch("video_analyzer.yt_dlp.YoutubeDL", return_value=ydl):
            result = VideoAnalyzer.get_video_info(
                "https://www.youtube.com/playlist?list=PL123"
            )
        self.assertTrue(result["is_playlist"])
        self.assertEqual(result["playlist_count"], 1)
        self.assertEqual(result["entries"][0]["id"], "abcdefghijk")


class FormatSelectorTests(unittest.TestCase):
    def setUp(self):
        self.formats = [
            {"format_id": "c360", "url": "https://example.test/c360", "height": 360,
             "width": 640, "fps": 30, "vcodec": "avc1", "acodec": "mp4a",
             "ext": "mp4", "tbr": 500},
            {"format_id": "c720", "url": "https://example.test/c720", "height": 720,
             "width": 1280, "fps": 30, "vcodec": "avc1", "acodec": "mp4a",
             "ext": "mp4", "tbr": 2500},
            {"format_id": "v1080", "url": "https://example.test/v1080", "height": 1080,
             "width": 1920, "fps": 30, "vcodec": "avc1", "acodec": "none",
             "ext": "mp4", "tbr": 5000},
            {"format_id": "v1440", "url": "https://example.test/v1440", "height": 1440,
             "width": 2560, "fps": 60, "vcodec": "vp9", "acodec": "none",
             "ext": "webm", "tbr": 10000},
            {"format_id": "v2160", "url": "https://example.test/v2160", "height": 2160,
             "width": 3840, "fps": 60, "vcodec": "av01", "acodec": "none",
             "ext": "webm", "tbr": 18000},
            {"format_id": "a128", "url": "https://example.test/a128", "vcodec": "none",
             "acodec": "mp4a", "ext": "m4a", "abr": 128},
            {"format_id": "a160", "url": "https://example.test/a160", "vcodec": "none",
             "acodec": "opus", "ext": "webm"},
        ]

    def select(self, expression):
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            selector = ydl.build_format_selector(expression)
            info = {
                "_type": "video",
                "extractor": "youtube",
                "id": "test",
                "formats": [dict(item) for item in self.formats],
            }
            return list(selector(info))

    @staticmethod
    def selected_ids(results):
        return [
            item.get("format_id")
            for result in results
            for item in result.get("requested_formats", [result])
        ]

    def test_best_quality_uses_highest_video_and_audio_formats(self):
        expression = FormatSelector.get_format_string(
            "Video", "Best Available", "Best Original Audio", True
        )
        self.assertEqual(expression, "bv[acodec=none]+ba/b")
        self.assertEqual(self.selected_ids(self.select(expression)), ["v2160", "a160"])

    def test_resolution_modes_choose_best_video_at_or_below_requested_height(self):
        for quality, expected_id, expression in (
            ("1080p", "v1080", "bv[acodec=none][height<=1080]+ba/b[height<=1080]"),
            ("720p", "c720", "bv[acodec=none][height<=720]+ba/b[height<=720]"),
            ("1440p / 2K", "v1440", "bv[acodec=none][height<=1440]+ba/b[height<=1440]"),
            ("2160p / 4K", "v2160", "bv[acodec=none][height<=2160]+ba/b[height<=2160]"),
        ):
            with self.subTest(quality=quality):
                actual = FormatSelector.get_format_string(
                    "Video", quality, "Best Original Audio", True
                )
                self.assertEqual(actual, expression)
                expected_ids = (
                    ["c720"] if expected_id == "c720" else [expected_id, "a160"]
                )
                self.assertEqual(self.selected_ids(self.select(actual)), expected_ids)

    def test_720p_split_video_keeps_best_original_audio_when_available(self):
        self.formats.append({
            "format_id": "v720",
            "url": "https://example.test/v720",
            "height": 720,
            "width": 1280,
            "fps": 30,
            "vcodec": "avc1",
            "acodec": "none",
            "ext": "mp4",
            "tbr": 2200,
        })
        expression = FormatSelector.get_format_string(
            "Video", "720p", "Best Original Audio", True
        )
        self.assertEqual(self.selected_ids(self.select(expression)), ["v720", "a160"])

    def test_mp4_compatibility_is_separate_and_constrains_container(self):
        expression = FormatSelector.get_format_string(
            "Video", "Best MP4 Compatible", "Best Original Audio", True
        )
        self.assertEqual(expression, "bv[acodec=none][ext=mp4]+ba[ext=m4a]/b[ext=mp4]")
        self.assertEqual(self.selected_ids(self.select(expression)), ["v1080", "a128"])

    def test_without_ffmpeg_falls_back_to_best_combined_without_hidden_cap(self):
        expression = FormatSelector.get_format_string(
            "Video", "Best Available", "Best Original Audio", False
        )
        self.assertEqual(expression, "b")
        self.assertEqual(self.selected_ids(self.select(expression)), ["c720"])
        self.assertEqual(
            FormatSelector.get_format_string("Video", "1080p", "Best", False),
            "b[height<=1080]",
        )

    def test_best_audio_does_not_prefer_m4a_and_mp3_uses_best_source(self):
        expression = FormatSelector.get_format_string(
            "Audio Only", "Best", "Best Original Audio"
        )
        self.assertEqual(expression, "bestaudio")
        self.assertEqual(self.selected_ids(self.select(expression)), ["a160"])
        self.assertEqual(
            FormatSelector.get_format_string("Audio Only", "Best", "MP3 High Quality"),
            "bestaudio/best",
        )
        self.assertTrue(FormatSelector.requires_ffmpeg("Audio Only", "MP3 High Quality"))
        self.assertFalse(FormatSelector.requires_ffmpeg("Audio Only", "Best Original Audio"))

    def test_only_combined_formats_can_fall_back_without_ffmpeg(self):
        self.formats = self.formats[:2]
        expression = FormatSelector.get_format_string("Video", "Best Available", "Best", False)
        self.assertEqual(self.selected_ids(self.select(expression)), ["c720"])

    def test_only_combined_formats_use_yt_dlp_fallback_when_ffmpeg_exists(self):
        self.formats = self.formats[:2]
        expression = FormatSelector.get_format_string("Video", "Best Available", "Best", True)
        self.assertEqual(self.selected_ids(self.select(expression)), ["c720"])

    def test_missing_bitrate_and_filesize_do_not_limit_best_stream_selection(self):
        self.assertEqual(
            self.selected_ids(self.select(FormatSelector.get_format_string(
                "Video", "Best Available", "Best Original Audio", True
            ))),
            ["v2160", "a160"],
        )

    def test_high_frame_rate_is_retained_and_codec_is_not_restricted(self):
        selected = self.select(FormatSelector.get_format_string(
            "Video", "Best Available", "Best Original Audio", True
        ))
        self.assertEqual(selected[0]["requested_formats"][0]["fps"], 60)
        self.assertEqual(selected[0]["requested_formats"][0]["vcodec"], "av01")
        self.assertEqual(selected[0]["requested_formats"][0]["ext"], "webm")

    def test_ffmpeg_discovery_accepts_configured_pair_and_checks_both_executables(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()
            with self.assertLogs("format_selector", level="INFO") as logs:
                with patch("format_selector.subprocess.run") as run:
                    tools = FormatSelector.discover_ffmpeg(
                        str(root), application_dir=root
                    )
        self.assertEqual(Path(tools.ffmpeg), ffmpeg.resolve())
        self.assertEqual(Path(tools.ffprobe), ffprobe.resolve())
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))
        self.assertIn(tools.ffmpeg, "\n".join(logs.output))
        self.assertIn(tools.ffprobe, "\n".join(logs.output))

    def test_ffmpeg_is_unavailable_without_both_binaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ffmpeg.exe").touch()
            with patch.dict("os.environ", {"LOCALAPPDATA": str(root), "PATH": ""}):
                with patch.object(FormatSelector, "_registered_windows_path", return_value=""):
                    with patch("format_selector.shutil.which", return_value=None):
                        self.assertIsNone(
                            FormatSelector.discover_ffmpeg(
                                str(root), application_dir=root
                            )
                        )

    def test_ffmpeg_discovery_logs_which_version_check_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ffmpeg.exe").touch()
            (root / "ffprobe.exe").touch()
            with patch("format_selector.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg.exe", 5)):
                with self.assertLogs("format_selector", level="INFO") as logs:
                    tools = FormatSelector.discover_ffmpeg(str(root), application_dir=root)
        self.assertIsNone(tools)
        self.assertIn("FFmpeg version check failed", "\n".join(logs.output))

    def test_ffmpeg_discovery_reuses_successful_check_while_binaries_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "ffmpeg.exe").touch()
            (root / "ffprobe.exe").touch()
            with patch("format_selector.subprocess.run") as run:
                first = FormatSelector.discover_ffmpeg(str(root), application_dir=root)
            with patch(
                "format_selector.subprocess.run",
                side_effect=subprocess.TimeoutExpired("ffmpeg.exe", 5),
            ) as retry:
                second = FormatSelector.discover_ffmpeg(str(root), application_dir=root)
        self.assertIsNotNone(first)
        self.assertEqual(second, first)
        retry.assert_not_called()
        self.assertEqual(run.call_count, 2)

    def test_ffmpeg_discovery_accepts_system_path_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ffmpeg = root / "ffmpeg.exe"
            ffprobe = root / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()
            with patch.dict("os.environ", {"LOCALAPPDATA": str(root), "PATH": ""}):
                with patch.object(FormatSelector, "_registered_windows_path", return_value=""):
                    with patch(
                        "format_selector.shutil.which",
                        side_effect=[str(ffmpeg), str(ffprobe)],
                    ):
                        with patch("format_selector.subprocess.run"):
                            tools = FormatSelector.discover_ffmpeg(
                                application_dir=root / "no-bundle"
                            )
        self.assertEqual(Path(tools.ffmpeg), ffmpeg.resolve())
        self.assertEqual(Path(tools.ffprobe), ffprobe.resolve())

    def test_ffmpeg_discovery_uses_persisted_path_when_process_path_is_stale(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "installed" / "bin"
            bin_dir.mkdir(parents=True)
            ffmpeg = bin_dir / "ffmpeg.exe"
            ffprobe = bin_dir / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()
            with patch.dict("os.environ", {"LOCALAPPDATA": str(root), "PATH": ""}):
                with patch.object(
                    FormatSelector,
                    "_registered_windows_path",
                    return_value=str(bin_dir),
                ):
                    with patch("format_selector.subprocess.run"):
                        tools = FormatSelector.discover_ffmpeg(
                            application_dir=root / "no-bundle"
                        )
        self.assertEqual(Path(tools.ffmpeg), ffmpeg.resolve())
        self.assertEqual(Path(tools.ffprobe), ffprobe.resolve())

    def test_ffprobe_runner_uses_resolved_executable_path(self):
        tools = FFmpegTools("C:\\tools\\ffmpeg.exe", "C:\\tools\\ffprobe.exe")
        with patch("format_selector.subprocess.run") as run:
            tools.run_ffprobe(["-version"], check=True)
        run.assert_called_once_with(
            ["C:\\tools\\ffprobe.exe", "-version"],
            check=True,
        )

    def test_ffmpeg_discovery_checks_bundled_bin_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            app_dir = Path(temporary)
            bundled_bin = app_dir / "ffmpeg" / "bin"
            bundled_bin.mkdir(parents=True)
            ffmpeg = bundled_bin / "ffmpeg.exe"
            ffprobe = bundled_bin / "ffprobe.exe"
            ffmpeg.touch()
            ffprobe.touch()
            with patch("format_selector.shutil.which", return_value=None):
                with patch("format_selector.subprocess.run"):
                    tools = FormatSelector.discover_ffmpeg(application_dir=app_dir)
        self.assertEqual(Path(tools.ffmpeg), ffmpeg.resolve())
        self.assertEqual(Path(tools.ffprobe), ffprobe.resolve())


class VideoDownloadManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.manager = DownloadManager(
            history_file=root / "history.json",
            state_file=root / "state.json",
        )

    def tearDown(self):
        self.manager.session_manager.close_all_sessions()
        self.temp_dir.cleanup()

    def _add_video(self, status="Downloading"):
        download = Download(
            "https://www.youtube.com/watch?v=abcdefghijk",
            self.temp_dir.name,
            "video-1",
            is_video=True,
        )
        download.status = status
        self.manager.downloads[download.download_id] = download
        return download

    def test_progress_hook_reports_filename_and_fragment_progress(self):
        download = self._add_video()
        self.manager._ytdlp_progress_hook(download.download_id, {
            "status": "downloading",
            "downloaded_bytes": 50,
            "total_bytes": 100,
            "filename": str(Path(self.temp_dir.name) / "sample.webm"),
            "fragment_index": 2,
            "fragment_count": 4,
        })
        time.sleep(0.02)
        self.manager._ytdlp_progress_hook(download.download_id, {
            "status": "downloading",
            "downloaded_bytes": 70,
            "total_bytes": 100,
            "filename": str(Path(self.temp_dir.name) / "sample.webm"),
            "fragment_index": 3,
            "fragment_count": 4,
        })
        self.assertEqual(download.status, "Downloading")
        self.assertEqual(download.downloaded, 70)
        self.assertEqual(download.file_size, 100)
        self.assertEqual(download.current_filename, "sample.webm")
        self.assertGreater(download.eta, 0)
        self.assertEqual(download.fragment_index, 3)
        self.assertEqual(download.fragment_count, 4)

    def test_cancel_requested_stops_progress_hook(self):
        download = self._add_video()
        download.cancel_requested = True
        with self.assertRaises(DownloadCancelled):
            self.manager._ytdlp_progress_hook(download.download_id, {"status": "downloading"})

    def test_progress_from_an_old_execution_is_ignored(self):
        download = self._add_video()
        download.execution_generation = 2
        download.downloaded = 7

        self.manager._ytdlp_progress_hook(
            download.download_id,
            {"status": "downloading", "downloaded_bytes": 99, "total_bytes": 100},
            generation=1,
        )

        self.assertEqual(download.downloaded, 7)

    def test_queued_cancellation_preserves_partial_file(self):
        download = self._add_video(status="Queued")
        partial = Path(self.temp_dir.name) / "sample.webm.part"
        partial.write_bytes(b"resume me")
        download.part_path = str(partial)

        self.manager.cancel_download(download.download_id)

        self.assertEqual(download.status, "Cancelled")
        self.assertEqual(partial.read_bytes(), b"resume me")

    def test_restart_restores_active_video_as_interrupted(self):
        download = self._add_video(status="Merging")
        download.current_filename = "sample.mkv"
        download.video_quality = "Best Available"
        download.playlist_group_id = "playlist-x"
        download.playlist_entry_id = "abcdefghijk"
        download.video_id = "abcdefghijk"
        self.manager._persist_downloads()

        recovered = DownloadManager(
            history_file=Path(self.temp_dir.name) / "history.json",
            state_file=Path(self.temp_dir.name) / "state.json",
        )
        self.addCleanup(recovered.session_manager.close_all_sessions)

        self.assertEqual(recovered.get_download_info(download.download_id)["status"], "Interrupted")
        self.assertEqual(recovered.downloads[download.download_id].video_quality, "Best Available")
        self.assertEqual(recovered.downloads[download.download_id].video_id, "abcdefghijk")
        self.assertEqual(recovered.downloads[download.download_id].playlist_entry_id, "abcdefghijk")

    def test_stale_execution_cannot_release_new_generation_slot(self):
        download = self._add_video()
        download.slot_active = True
        download.slot_generation = 4
        download.execution_generation = 4
        self.manager.active_downloads = 1

        self.manager._release_active_slot(download.download_id, generation=3)

        self.assertTrue(download.slot_active)
        self.assertEqual(self.manager.active_downloads, 1)

    def test_resume_requests_while_paused_enqueue_only_once(self):
        download = self._add_video(status="Paused")
        with patch.object(self.manager, "_process_queue"):
            self.manager.resume_download(download.download_id)
            self.manager.resume_download(download.download_id)

        self.assertEqual(download.status, "Queued")
        self.assertEqual(self.manager.download_queue.qsize(), 1)

    def test_completion_rejects_part_files_and_old_cancelled_generation(self):
        download = self._add_video(status="Cancelled")
        download.execution_generation = 2
        partial = Path(self.temp_dir.name) / "output.mkv.part"
        partial.write_bytes(b"partial")

        self.manager._complete_ytdlp_output(download.download_id, 2, str(partial))

        self.assertEqual(download.status, "Cancelled")

    def test_completion_callback_is_issued_only_once(self):
        download = self._add_video()
        download.execution_generation = 2
        output = Path(self.temp_dir.name) / "complete.mkv"
        output.write_bytes(b"media")
        completed = []
        self.manager.register_status_callback(
            lambda download_id, status: completed.append((download_id, status))
            if status == "Completed" else None
        )

        self.manager._complete_ytdlp_output(download.download_id, 2, str(output))
        self.manager._complete_ytdlp_output(download.download_id, 2, str(output))

        self.assertEqual(completed, [(download.download_id, "Completed")])

    def test_invalid_ffprobe_streams_preserve_output_and_fail_completion(self):
        download = self._add_video()
        download.execution_generation = 2
        output = Path(self.temp_dir.name) / "missing-audio.mkv"
        output.write_bytes(b"diagnostic media")
        tools = FFmpegTools("C:\\tools\\ffmpeg.exe", "C:\\tools\\ffprobe.exe")

        with patch(
            "download_manager.validate_media_output",
            return_value=MediaValidationResult("invalid", "Required audio stream missing"),
        ):
            self.manager._complete_ytdlp_output(
                download.download_id, 2, str(output), ffmpeg_tools=tools
            )

        self.assertEqual(download.status, "Error")
        self.assertEqual(download.error_category, "OUTPUT_VALIDATION_ERROR")
        self.assertTrue(output.exists())

    def test_missing_ffprobe_allows_completion_and_records_unavailable(self):
        download = self._add_video()
        download.execution_generation = 2
        output = Path(self.temp_dir.name) / "valid-unprobed.mkv"
        output.write_bytes(b"media")

        with patch(
            "download_manager.validate_media_output",
            return_value=MediaValidationResult("unavailable", "ffprobe is not available"),
        ):
            self.manager._complete_ytdlp_output(download.download_id, 2, str(output))

        self.assertEqual(download.status, "Completed")
        self.assertEqual(download.media_validation_status, "unavailable")

    def test_cancellation_during_ffprobe_validation_prevents_completion(self):
        download = self._add_video()
        download.execution_generation = 2
        output = Path(self.temp_dir.name) / "cancel-before-validation.mkv"
        output.write_bytes(b"media")

        def cancel_during_probe(*_args, **_kwargs):
            self.manager.cancel_download(download.download_id)
            return MediaValidationResult("valid", "streams present")

        with patch("download_manager.validate_media_output", side_effect=cancel_during_probe):
            self.manager._complete_ytdlp_output(download.download_id, 2, str(output))

        self.assertEqual(download.status, "Cancelled")

    def test_stale_generation_is_rejected_before_ffprobe_validation(self):
        download = self._add_video()
        download.execution_generation = 3
        output = Path(self.temp_dir.name) / "stale.mkv"
        output.write_bytes(b"media")

        with patch("download_manager.validate_media_output") as validate:
            self.manager._complete_ytdlp_output(download.download_id, 2, str(output))

        validate.assert_not_called()
        self.assertEqual(download.status, "Downloading")

    def test_part_only_or_missing_output_cannot_complete(self):
        download = self._add_video()
        download.execution_generation = 2
        part = Path(self.temp_dir.name) / "not-final.mkv.part"
        part.write_bytes(b"partial")

        self.manager._complete_ytdlp_output(download.download_id, 2, str(part))
        self.assertEqual(download.status, "Error")

        download.status = "Downloading"
        self.manager._complete_ytdlp_output(
            download.download_id, 2, str(Path(self.temp_dir.name) / "missing.mkv")
        )
        self.assertEqual(download.status, "Error")

    def test_merge_renamed_output_uses_yt_dlp_returned_path(self):
        download = self._add_video()
        download.video_quality = "Best MP4 Compatible"
        observed = {}
        output_path = Path(self.temp_dir.name) / "renamed-compatible.mp4"

        class RenamedYoutubeDL:
            def __init__(self, options):
                observed["options"] = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                output_path.write_bytes(b"merged")
                return {"filepath": str(output_path), "format_id": "video+audio"}

        with patch.object(
            FormatSelector,
            "discover_ffmpeg",
            return_value=FFmpegTools("C:\\tools\\ffmpeg.exe", "C:\\tools\\ffprobe.exe"),
        ):
            with patch("download_manager.yt_dlp.YoutubeDL", RenamedYoutubeDL):
                self.manager._download_video_with_ytdlp(download.download_id)
                if download.worker:
                    download.worker.join(timeout=3)

        self.assertEqual(download.status, "Completed", download.error_message)
        self.assertEqual(Path(download.download_path).name, "renamed-compatible.mp4")

    def test_ffmpeg_postprocessor_failure_is_permanent_and_classified(self):
        class FailedMergeYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                raise RuntimeError("ffmpeg merge failed")

        download = self._add_video()
        with patch.object(
            FormatSelector,
            "discover_ffmpeg",
            return_value=FFmpegTools("C:\\tools\\ffmpeg.exe", "C:\\tools\\ffprobe.exe"),
        ):
            with patch("download_manager.yt_dlp.YoutubeDL", FailedMergeYoutubeDL):
                self.manager._download_video_with_ytdlp(download.download_id)
                if download.worker:
                    download.worker.join(timeout=3)

        self.assertEqual(download.status, "Error")
        self.assertEqual(download.error_category, "FFMPEG_ERROR")
        self.assertEqual(download.retry_count, 0)

    def test_ytdlp_error_classification_separates_transient_and_permanent(self):
        self.assertEqual(
            self.manager._classify_ytdlp_error(TimeoutError("socket timed out")),
            "TIMEOUT",
        )
        self.assertEqual(
            self.manager._classify_ytdlp_error(RuntimeError("private video")),
            "PRIVATE_VIDEO",
        )

    def test_duplicate_active_video_request_is_rejected_but_other_quality_is_allowed(self):
        url = "https://www.youtube.com/watch?v=abcdefghijk&list=PL123"
        with patch.object(self.manager, "_process_queue"):
            self.manager.add_download(url, self.temp_dir.name, video_quality="Best Available")
            with self.assertRaises(ValueError):
                self.manager.add_download(
                    "https://youtu.be/abcdefghijk",
                    self.temp_dir.name,
                    video_quality="Best Available",
                )
            other_quality_id = self.manager.add_download(
                "https://www.youtube.com/watch?v=abcdefghijk",
                self.temp_dir.name,
                video_quality="1080p",
            )

        self.assertIn(other_quality_id, self.manager.downloads)

    def test_ytdlp_timeout_retries_once_then_stops_without_quality_downgrade(self):
        self.manager.max_retries = 1
        calls = []

        class FailingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                calls.append(download)
                raise TimeoutError("network timed out")

        with patch.object(FormatSelector, "discover_ffmpeg", return_value=None):
            with patch("download_manager.discover_deno", return_value=None):
                with patch("download_manager.yt_dlp.YoutubeDL", FailingYoutubeDL):
                    download_id = self.manager.add_download(
                        "https://www.youtube.com/watch?v=abcdefghijk",
                        self.temp_dir.name,
                        video_quality="Best Available",
                    )
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        current = self.manager.downloads[download_id]
                        if (current.execution_generation >= 2 and current.status == "Error"
                                and not current.slot_active):
                            break
                        time.sleep(0.01)
        download = self.manager.downloads[download_id]
        self.assertEqual(download.status, "Error")
        self.assertEqual(download.retry_count, 1)
        self.assertEqual(download.execution_generation, 2)
        self.assertEqual(download.video_quality, "Best Available")
        self.assertEqual(calls, [True, True])

    def test_cancel_during_metadata_extraction_becomes_terminal_after_extractor_returns(self):
        entered = threading.Event()
        finish = threading.Event()

        class WaitingYoutubeDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                entered.set()
                finish.wait(2)
                return {"id": "abcdefghijk"}

        with patch.object(FormatSelector, "discover_ffmpeg", return_value=None):
            with patch("download_manager.discover_deno", return_value=None):
                with patch("download_manager.yt_dlp.YoutubeDL", WaitingYoutubeDL):
                    download_id = self.manager.add_download(
                        "https://www.youtube.com/watch?v=abcdefghijk", self.temp_dir.name
                    )
                    self.assertTrue(entered.wait(2))
                    self.manager.cancel_download(download_id)
                    finish.set()
                    deadline = time.time() + 3
                    while time.time() < deadline:
                        item = self.manager.downloads[download_id]
                        if item.status == "Cancelled" and not item.slot_active:
                            break
                        time.sleep(0.01)

        self.assertEqual(self.manager.downloads[download_id].status, "Cancelled")
        self.assertEqual(self.manager.active_downloads, 0)

    def test_cancel_during_merge_stops_postprocessor_callback_and_releases_slot(self):
        merge_started = threading.Event()
        continue_merge = threading.Event()

        class WaitingMergeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                hook = self.options["postprocessor_hooks"][0]
                hook({"status": "started", "postprocessor": "Merger"})
                merge_started.set()
                continue_merge.wait(2)
                hook({"status": "finished", "postprocessor": "Merger"})

        with patch.object(FormatSelector, "discover_ffmpeg", return_value=None):
            with patch("download_manager.discover_deno", return_value=None):
                with patch("download_manager.yt_dlp.YoutubeDL", WaitingMergeYoutubeDL):
                    download_id = self.manager.add_download(
                        "https://www.youtube.com/watch?v=abcdefghijk", self.temp_dir.name
                    )
                    self.assertTrue(merge_started.wait(2))
                    self.manager.cancel_download(download_id)
                    continue_merge.set()
                    deadline = time.time() + 3
                    while time.time() < deadline:
                        item = self.manager.downloads[download_id]
                        if item.status == "Cancelled" and not item.slot_active:
                            break
                        time.sleep(0.01)

        self.assertEqual(self.manager.downloads[download_id].status, "Cancelled")
        self.assertEqual(self.manager.active_downloads, 0)

    def test_cancel_near_100_percent_wins_over_late_completion(self):
        progress_sent = threading.Event()
        continue_download = threading.Event()

        class NearCompleteYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                hook = self.options["progress_hooks"][0]
                hook({"status": "downloading", "downloaded_bytes": 100, "total_bytes": 100})
                progress_sent.set()
                continue_download.wait(2)
                hook({"status": "finished", "total_bytes": 100})

        with patch.object(FormatSelector, "discover_ffmpeg", return_value=None):
            with patch("download_manager.discover_deno", return_value=None):
                with patch("download_manager.yt_dlp.YoutubeDL", NearCompleteYoutubeDL):
                    download_id = self.manager.add_download(
                        "https://www.youtube.com/watch?v=abcdefghijk", self.temp_dir.name
                    )
                    self.assertTrue(progress_sent.wait(2))
                    self.manager.cancel_download(download_id)
                    continue_download.set()
                    deadline = time.time() + 3
                    while time.time() < deadline:
                        item = self.manager.downloads[download_id]
                        if item.status == "Cancelled" and not item.slot_active:
                            break
                        time.sleep(0.01)

        self.assertEqual(self.manager.downloads[download_id].status, "Cancelled")
        self.assertEqual(self.manager.active_downloads, 0)

    def test_playlist_failure_and_retry_affect_only_that_item(self):
        first = self._add_video()
        second = Download(
            "https://www.youtube.com/watch?v=123456789ab",
            self.temp_dir.name,
            "video-2",
            is_video=True,
        )
        first.playlist_group_id = second.playlist_group_id = "playlist-isolation"
        first.status = second.status = "Downloading"
        self.manager.downloads[second.download_id] = second

        self.manager._transition_download(first.download_id, "Error")
        with patch.object(self.manager, "_process_queue"):
            self.manager.resume_download(first.download_id)

        self.assertEqual(first.status, "Queued")
        self.assertEqual(second.status, "Downloading")

    def test_missing_ffmpeg_fails_before_mp3_download_starts(self):
        download = self._add_video()
        download.download_type = "Audio Only"
        download.audio_quality = "MP3 High Quality"
        with patch.object(FormatSelector, "discover_ffmpeg", return_value=None):
            self.manager._download_video_with_ytdlp(download.download_id)
        self.assertEqual(download.status, "Error")
        self.assertIn("requires FFmpeg", download.error_message)

    def test_completion_waits_for_mocked_yt_dlp_postprocessing(self):
        download = self._add_video()
        download.subtitle_languages = ["en"]
        download.download_subtitles = True
        download.embed_subtitles = True
        download.save_thumbnail = True
        download.embed_thumbnail = True
        download.embed_metadata = True
        observed = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                observed["options"] = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                assert download is True
                options = observed["options"]
                output_path = Path(options["outtmpl"].replace("%(ext)s", "mkv"))
                options["progress_hooks"][0]({
                    "status": "downloading",
                    "downloaded_bytes": 4,
                    "total_bytes": 4,
                    "filename": str(output_path),
                })
                options["postprocessor_hooks"][0]({
                    "status": "started",
                    "postprocessor": "Merger",
                })
                self.assert_merging()
                output_path.write_bytes(b"data")
                options["postprocessor_hooks"][0]({
                    "status": "finished",
                    "postprocessor": "Merger",
                })
                return {
                    "requested_formats": [
                        {
                            "format_id": "315",
                            "width": 3840,
                            "height": 2160,
                            "fps": 60,
                            "vcodec": "vp9",
                            "acodec": "none",
                            "ext": "webm",
                        },
                        {
                            "format_id": "251",
                            "vcodec": "none",
                            "acodec": "opus",
                            "abr": 160,
                            "ext": "webm",
                        },
                    ],
                    "format_id": "315+251",
                }

            @staticmethod
            def assert_merging():
                assert download.status == "Merging"

        with patch.object(
            FormatSelector,
            "discover_ffmpeg",
            return_value=FFmpegTools("C:\\tools\\ffmpeg.exe", "C:\\tools\\ffprobe.exe"),
        ):
            with patch(
                "download_manager.discover_deno",
                return_value=DenoRuntime("C:\\tools\\deno.exe", "2.3.0"),
            ):
                with patch("download_manager.youtube_session_options", return_value={"cookiesfrombrowser": ("edge",)}), patch("download_manager.yt_dlp.YoutubeDL", FakeYoutubeDL):
                    self.manager._download_video_with_ytdlp(download.download_id)

        deadline = time.time() + 5
        while download.status not in {"Completed", "Error"} and time.time() < deadline:
            time.sleep(0.01)
        if download.worker:
            download.worker.join(timeout=5)
        self.assertEqual(download.status, "Completed", download.error_message)
        self.assertTrue(download.download_path.endswith(".mkv"))
        self.assertTrue(observed["options"]["noplaylist"])
        self.assertEqual(observed["options"]["cookiesfrombrowser"], ("edge",))
        self.assertTrue(observed["options"]["continuedl"])
        self.assertFalse(observed["options"]["nopart"])
        self.assertEqual(observed["options"]["format"], "bv[acodec=none]+ba/b")
        self.assertEqual(observed["options"]["merge_output_format"], "mkv")
        self.assertEqual(observed["options"]["ffmpeg_location"], "C:\\tools\\ffmpeg.exe")
        self.assertEqual(
            observed["options"]["js_runtimes"],
            {"deno": {"path": "C:\\tools\\deno.exe"}},
        )
        self.assertNotIn("remote_components", observed["options"])
        self.assertNotIn("postprocessors", observed["options"])
        self.assertEqual(observed["options"]["subtitleslangs"], ["en"])
        self.assertTrue(observed["options"]["writesubtitles"])
        self.assertTrue(observed["options"]["writeautomaticsub"])
        self.assertTrue(observed["options"]["embedsubtitles"])
        self.assertTrue(observed["options"]["writethumbnail"])
        self.assertTrue(observed["options"]["embedthumbnail"])
        self.assertTrue(observed["options"]["addmetadata"])
        self.assertEqual(download.selected_video_format["format_id"], "315")
        self.assertEqual(download.selected_audio_format["format_id"], "251")
        self.assertEqual(
            download.selected_format_summary,
            "2160p • 60 FPS • VP9 • Opus • 160 kbps",
        )
        self.assertEqual(
            self.manager.get_download_info(download.download_id)["quality"],
            "2160p • 60 FPS • VP9 • Opus • 160 kbps",
        )

    def test_mp3_download_uses_best_source_and_highest_vbr_quality(self):
        download = self._add_video()
        download.download_type = "Audio Only"
        download.audio_quality = "MP3 High Quality"
        observed = {}

        class FakeYoutubeDL:
            def __init__(self, options):
                observed["options"] = options

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download):
                assert download is True
                output_path = Path(
                    observed["options"]["outtmpl"].replace("%(ext)s", "mp3")
                )
                output_path.write_bytes(b"mp3")
                return {
                    "format_id": "251",
                    "vcodec": "none",
                    "acodec": "opus",
                    "abr": 160,
                    "ext": "webm",
                }

        with patch.object(
            FormatSelector,
            "discover_ffmpeg",
            return_value=FFmpegTools("C:\\tools\\ffmpeg.exe", "C:\\tools\\ffprobe.exe"),
        ):
            with patch("download_manager.yt_dlp.YoutubeDL", FakeYoutubeDL):
                self.manager._download_video_with_ytdlp(download.download_id)
        if download.worker:
            download.worker.join(timeout=5)

        self.assertEqual(download.status, "Completed", download.error_message)
        self.assertEqual(observed["options"]["format"], "bestaudio/best")
        self.assertEqual(
            observed["options"]["postprocessors"][0]["preferredquality"],
            "0",
        )
        self.assertEqual(
            download.selected_format_summary,
            "MP3 VBR 0 • source Opus",
        )

    def test_optional_media_options_require_explicit_selections(self):
        download = self._add_video()
        self.assertEqual(self.manager._optional_ytdlp_options(download), {})

        download.subtitle_languages = ["en", "fr"]
        download.download_subtitles = True
        download.embed_subtitles = True
        download.save_thumbnail = True
        download.embed_thumbnail = True
        download.embed_metadata = True
        options = self.manager._optional_ytdlp_options(download)

        self.assertEqual(options["subtitleslangs"], ["en", "fr"])
        self.assertTrue(options["writesubtitles"])
        self.assertTrue(options["writeautomaticsub"])
        self.assertTrue(options["embedsubtitles"])
        self.assertTrue(options["writethumbnail"])
        self.assertTrue(options["embedthumbnail"])
        self.assertTrue(options["addmetadata"])

    def test_subtitles_are_never_enabled_without_selected_languages(self):
        download = self._add_video()
        download.download_subtitles = True
        download.embed_subtitles = True

        self.assertEqual(self.manager._optional_ytdlp_options(download), {})

    def test_optional_postprocessor_failure_preserves_valid_media(self):
        download = self._add_video(status="Processing")
        download.active_postprocessor = "FFmpegEmbedSubtitle"
        output = Path(self.temp_dir.name) / "sample.mkv"
        output.write_bytes(b"verified media")
        download.final_output_path = str(output)
        generation = download.execution_generation

        with patch(
            "download_manager.validate_media_output",
            return_value=MediaValidationResult("valid", "streams present"),
        ):
            recovered = self.manager._recover_optional_postprocessor_failure(
                download,
                RuntimeError(
                    "subtitle failed for https://cdn.example.test/file?token=private-value"
                ),
                generation,
                FFmpegTools("ffmpeg", "ffprobe"),
            )

        self.assertTrue(recovered)
        self.assertEqual(download.status, "Completed")
        self.assertEqual(download.media_validation_status, "valid")
        self.assertTrue(download.optional_media_warnings)
        self.assertNotIn("private-value", " ".join(download.optional_media_warnings))

    def test_video_url_routes_to_yt_dlp_without_starting_network_work(self):
        with patch.object(self.manager, "_process_queue"):
            download_id = self.manager.add_download(
                "https://www.youtube.com/watch?v=abcdefghijk",
                self.temp_dir.name,
            )
        self.assertTrue(self.manager.downloads[download_id].is_video)

    def test_recovery_rejects_dotdot_filename_before_video_output_path_is_built(self):
        state_path = Path(self.temp_dir.name) / "malicious-state.json"
        history_path = Path(self.temp_dir.name) / "malicious-history.json"
        state_path.write_text(
            __import__("json").dumps([{
                "id": "recovered-video",
                "url": "https://www.youtube.com/watch?v=abcdefghijk",
                "destination_dir": self.temp_dir.name,
                "is_video": True,
                "current_filename": "..",
                "status": "Interrupted",
            }]),
            encoding="utf-8",
        )
        recovered = DownloadManager(history_file=history_path, state_file=state_path)
        try:
            download = recovered.downloads["recovered-video"]
            self.assertIsNone(download.current_filename)
            self.assertEqual(download.download_path, os.path.abspath(self.temp_dir.name))
        finally:
            recovered.session_manager.close_all_sessions()

    def test_safe_video_filename_guard_rejects_windows_paths_and_reserved_names(self):
        for unsafe in (".", "..", "CON.txt", "C:\\outside.mkv", "sub\\video.mkv", "trailing."):
            with self.subTest(filename=unsafe):
                self.assertFalse(DownloadManager._is_safe_download_filename(unsafe))
        self.assertTrue(DownloadManager._is_safe_download_filename("video (1).mkv"))

    def test_history_redacts_sensitive_query_values_from_url_and_error(self):
        self.manager._add_history_entry({
            "id": "sensitive-history",
            "status": "Error",
            "url": "https://media.example.test/v?api_key=secret",
            "source_url": "https://media.example.test/v?client_secret=hidden",
            "error": "request failed: https://media.example.test/v?lsig=signed",
        })
        saved = next(item for item in self.manager.history if item["id"] == "sensitive-history")
        self.assertNotIn("=secret", saved["url"])
        self.assertNotIn("=hidden", saved["source_url"])
        self.assertNotIn("=signed", saved["error"])
        self.assertIn("[REDACTED]", saved["url"])

    def test_selected_playlist_items_report_aggregate_progress(self):
        first = self._add_video()
        second = Download(
            "https://www.youtube.com/watch?v=123456789ab",
            self.temp_dir.name,
            "video-2",
            is_video=True,
        )
        first.playlist_group_id = second.playlist_group_id = "playlist-1"
        first.playlist_title = second.playlist_title = "Test playlist"
        first.playlist_index, second.playlist_index = 1, 2
        first.playlist_count = second.playlist_count = 2
        first.downloaded, first.file_size = 50, 100
        second.downloaded, second.file_size = 0, 100
        self.manager.downloads[second.download_id] = second
        info = self.manager.get_download_info(first.download_id)
        self.assertEqual(info["playlist_title"], "Test playlist")
        self.assertEqual(info["playlist_progress"], 25)
        self.assertTrue(info["playlist_progress_estimated"])


if __name__ == "__main__":
    unittest.main()
