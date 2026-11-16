import unittest

from error_handler import UserFriendlyError
from download_manager import DownloadManager


class UserFriendlyErrorTests(unittest.TestCase):
    def test_known_download_categories_have_short_safe_messages(self):
        categories = (
            "NETWORK_ERROR", "TIMEOUT", "TEMPORARY_HTTP_ERROR", "VIDEO_UNAVAILABLE",
            "PRIVATE_VIDEO", "LOGIN_REQUIRED", "AGE_RESTRICTED", "FORMAT_UNAVAILABLE",
            "FFMPEG_MISSING", "FFMPEG_ERROR", "JS_RUNTIME_ERROR", "POSTPROCESSING_ERROR",
            "DISK_FULL", "PERMISSION_ERROR", "OUTPUT_VALIDATION_ERROR", "CANCELLED",
            "UNKNOWN_ERROR",
        )
        for category in categories:
            with self.subTest(category=category):
                info = UserFriendlyError.get_user_friendly_message(
                    RuntimeError("https://example.test/?token=secret\n" + "traceback " * 100),
                    error_category=category,
                )
                self.assertTrue(info["user_message"])
                self.assertNotIn("secret", info["user_message"])
                self.assertLess(len(info["user_message"]), 180)

    def test_ordinary_display_omits_technical_tracebacks(self):
        info = UserFriendlyError.get_user_friendly_message(
            RuntimeError("private implementation traceback details"),
            error_category="UNKNOWN_ERROR",
        )

        display = UserFriendlyError.format_error_display(info)

        self.assertNotIn("private implementation traceback details", display)

    def test_video_error_classifier_maps_common_categories(self):
        cases = {
            "connection reset by peer": "NETWORK_ERROR",
            "request timed out": "TIMEOUT",
            "HTTP Error 429: Too Many Requests": "TEMPORARY_HTTP_ERROR",
            "HTTP Error 408: Request Timeout": "TEMPORARY_HTTP_ERROR",
            "Video unavailable": "VIDEO_UNAVAILABLE",
            "Private video": "PRIVATE_VIDEO",
            "Sign in to confirm": "LOGIN_REQUIRED",
            "This video is age-restricted": "AGE_RESTRICTED",
            "Requested format is not available": "FORMAT_UNAVAILABLE",
            "ffmpeg was not found": "FFMPEG_MISSING",
            "ffmpeg is not configured": "FFMPEG_MISSING",
            "ffmpeg failed to process file": "FFMPEG_ERROR",
            "JavaScript runtime unavailable: Deno": "JS_RUNTIME_ERROR",
            "Postprocessor failed": "POSTPROCESSING_ERROR",
            "No space left on device": "DISK_FULL",
            "Permission denied": "PERMISSION_ERROR",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(
                    DownloadManager._classify_ytdlp_error(RuntimeError(message)), expected
                )


if __name__ == "__main__":
    unittest.main()
