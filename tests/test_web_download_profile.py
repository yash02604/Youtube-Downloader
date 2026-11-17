"""Offline checks for opt-in compatibility hooks on real core extraction paths."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from download_manager import Download, DownloadManager
from video_analyzer import VideoAnalyzer


URL = "https://www.youtube.com/watch?v=abcdefghijk"
OWNED_OPTIONS = {
    "js_runtimes": {"deno": {"path": "test-deno"}},
    "extractor_args": {"youtube": {"player_client": ["mweb"]}},
    "remote_components": set(),
}


class WebDownloadProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.manager = DownloadManager(
            history_file=self.root / "history.json",
            state_file=self.root / "state.json",
            allow_browser_session=False,
        )

    def tearDown(self):
        self.manager.session_manager.close_all_sessions()
        self.temp_dir.cleanup()

    def run_download(self, *, profile=None, error=None):
        factory = MagicMock(return_value=profile)
        if profile is not None:
            self.manager.youtube_profile_factory = factory
        job = Download(URL, str(self.root), "video-1", is_video=True)
        job.status = "Downloading"
        self.manager.downloads[job.download_id] = job
        output = self.root / "sample.webm"
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl

        def extract(url, download):
            self.assertEqual(url, URL)
            self.assertTrue(download)
            if profile is not None:
                profile.prepare.assert_called_once_with(ydl)
            if error is not None:
                raise error
            output.write_bytes(b"offline media fixture")
            return {"filepath": str(output), "format_id": "18"}

        ydl.extract_info.side_effect = extract
        with patch("download_manager.discover_deno", return_value=None), patch(
            "download_manager.FormatSelector.discover_ffmpeg", return_value=None
        ), patch("download_manager.yt_dlp.YoutubeDL", return_value=ydl) as constructor, patch.object(
            self.manager, "_complete_ytdlp_output"
        ) as complete:
            self.manager._download_video_with_ytdlp(job.download_id)
            job.worker.join(timeout=3)
            self.assertFalse(job.worker.is_alive())
        return constructor.call_args.args[0], factory, complete, job

    def test_analysis_and_actual_download_use_same_owned_options_and_setup(self):
        profile = MagicMock(options=OWNED_OPTIONS)
        analysis_ydl = MagicMock()
        analysis_ydl.__enter__.return_value = analysis_ydl

        def extract(url, download):
            profile.prepare.assert_called_once_with(analysis_ydl)
            self.assertFalse(download)
            return {"id": "abcdefghijk", "title": "Sample"}

        analysis_ydl.extract_info.side_effect = extract
        with patch("video_analyzer.discover_deno", return_value=None), patch(
            "video_analyzer.yt_dlp.YoutubeDL", return_value=analysis_ydl
        ) as constructor:
            result = VideoAnalyzer.get_video_info(
                URL, allow_browser_session=False,
                ytdlp_options=profile.options, ytdlp_setup=profile.prepare,
            )
        self.assertEqual(result["title"], "Sample")
        analysis_options = constructor.call_args.args[0]
        profile.prepare.reset_mock()
        options, factory, complete, _ = self.run_download(profile=profile)
        factory.assert_called_once_with()
        profile.finish.assert_called_once_with(None)
        complete.assert_called_once()
        for key in OWNED_OPTIONS:
            self.assertEqual(options[key], analysis_options[key])
        self.assertEqual(options["retries"], 3)
        self.assertEqual(options["fragment_retries"], 3)
        self.assertEqual(options["concurrent_fragment_downloads"], 8)
        self.assertEqual(options["format"], "b")

    def test_download_failure_is_passed_to_finish_once(self):
        error = RuntimeError("offline extraction failure")
        profile = MagicMock(options=OWNED_OPTIONS)
        _, _, complete, job = self.run_download(profile=profile, error=error)
        profile.finish.assert_called_once_with(error)
        complete.assert_not_called()
        self.assertEqual(job.status, "Error")

    def test_each_download_constructs_and_prepares_a_fresh_profile(self):
        first = MagicMock(options=OWNED_OPTIONS)
        second = MagicMock(options=OWNED_OPTIONS)
        self.run_download(profile=first)
        self.run_download(profile=second)
        first.prepare.assert_called_once()
        second.prepare.assert_called_once()
        self.assertIsNot(first.prepare.call_args.args[0], second.prepare.call_args.args[0])
        first.finish.assert_called_once_with(None)
        second.finish.assert_called_once_with(None)

    def test_setup_failure_is_passed_to_finish_without_downloading(self):
        profile = MagicMock(options=OWNED_OPTIONS)
        error = RuntimeError("offline setup failure")
        profile.prepare.side_effect = error
        _, _, complete, job = self.run_download(profile=profile)
        profile.finish.assert_called_once_with(error)
        ydl = profile.prepare.call_args.args[0]
        ydl.extract_info.assert_not_called()
        complete.assert_not_called()
        self.assertEqual(job.status, "Error")

    def test_diagnostic_failure_does_not_replace_download_result(self):
        profile = MagicMock(options=OWNED_OPTIONS)
        profile.finish.side_effect = RuntimeError("sensitive diagnostic detail")
        with self.assertLogs("download_manager", level="WARNING") as captured:
            _, _, complete, _ = self.run_download(profile=profile)
        complete.assert_called_once()
        profile.finish.assert_called_once_with(None)
        self.assertEqual(
            captured.output,
            ["WARNING:download_manager:YouTube compatibility diagnostics failed"],
        )

    def test_default_desktop_download_does_not_construct_profile(self):
        options, factory, complete, _ = self.run_download()
        self.assertIsNone(self.manager.youtube_profile_factory)
        factory.assert_not_called()
        complete.assert_called_once()
        for key in OWNED_OPTIONS:
            self.assertNotIn(key, options)

    def test_default_analysis_extracts_without_setup(self):
        ydl = MagicMock()
        ydl.__enter__.return_value = ydl
        ydl.extract_info.return_value = {"id": "sample"}
        with patch("video_analyzer.discover_deno", return_value=None), patch(
            "video_analyzer.yt_dlp.YoutubeDL", return_value=ydl
        ):
            result = VideoAnalyzer.get_video_info(URL, allow_browser_session=False)
        self.assertEqual(result["id"], "sample")
        ydl.extract_info.assert_called_once_with(URL, download=False)


if __name__ == "__main__":
    unittest.main()
