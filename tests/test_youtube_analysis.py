"""Safe web-only YouTube observations, never live media requests."""
import json
import unittest
from unittest.mock import patch


class AnalysisDiagnosticsTests(unittest.TestCase):
    def test_explicit_error_categories_and_safe_types(self):
        from web.backend.youtube_analysis import classify_error
        for text, code, reason in (
            ('Sign in to confirm you are not a bot https://x/?token=secret', 'LOGIN_REQUIRED', 'BOT_VERIFICATION'),
            ('Please sign in to view this video', 'LOGIN_REQUIRED', 'SIGN_IN_REQUIRED'),
            ('A PO Token is required for this client', 'PO_TOKEN_REQUIRED', 'PO_TOKEN_REQUIRED'),
            ('HTTP Error 403: Forbidden cookie=secret', 'UPSTREAM_REJECTED', 'HTTP_403'),
            ('HTTP Error 429: Too Many Requests', 'UPSTREAM_REJECTED', 'HTTP_429'),
            ('No usable challenge solver lib script available', 'EJS_UNAVAILABLE', 'SOLVER_ASSETS_UNAVAILABLE'),
            ('Signature solving failed: C:/secret', 'ANALYSIS_FAILED', 'JS_CHALLENGE_FAILED'),
            ('C:/secret/file?signature=private', 'ANALYSIS_FAILED', 'UNKNOWN'),
        ):
            with self.subTest(code=code):
                self.assertEqual(classify_error(RuntimeError(text)), (code, reason))

    def test_raw_logger_content_never_reaches_logs(self):
        from web.backend.youtube_analysis import SafeYtdlpLogger
        logger = SafeYtdlpLogger()
        for method in (logger.debug, logger.warning, logger.error):
            method('https://media/?token=secret Cookie: private C:/secret/header')
        serialized = json.dumps(logger.snapshot())
        for value in ('secret', 'private', 'https://', 'Cookie', 'C:/'):
            self.assertNotIn(value, serialized)

    def test_solver_asset_failure_is_observable_without_raw_data(self):
        from web.backend.youtube_analysis import SafeYtdlpLogger
        observation = SafeYtdlpLogger()
        observation.warning('Failed to load challenge solver lib script from python package C:/secret')
        self.assertIn('SOLVER_ASSETS_UNAVAILABLE', observation.snapshot()['signals'])
        self.assertNotIn('secret', json.dumps(observation.snapshot()))

    def test_ejs_detection_uses_ytdlp_visibility(self):
        from web.backend.youtube_analysis import ejs_status
        with patch('yt_dlp.dependencies.yt_dlp_ejs', None):
            self.assertFalse(ejs_status()['visible_to_ytdlp'])
        with patch('yt_dlp.dependencies.yt_dlp_ejs', object()):
            self.assertTrue(ejs_status()['visible_to_ytdlp'])

    def test_wrapper_logs_only_allowlisted_diagnostics(self):
        from web.backend.youtube_analysis import analyze_public_youtube
        with patch('web.backend.youtube_analysis.VideoAnalyzer.get_video_info',
                   side_effect=RuntimeError('Sign in to confirm you are not a bot https://x/?token=secret')):
            with self.assertLogs('web.backend.youtube_analysis', level='INFO') as captured:
                with self.assertRaises(RuntimeError):
                    analyze_public_youtube('https://youtube.com/watch?v=Auvd4NPXHnI')
        text = '\n'.join(captured.output)
        self.assertIn('BOT_VERIFICATION', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('https://', text)


if __name__ == '__main__':
    unittest.main()
