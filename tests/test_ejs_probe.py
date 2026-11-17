"""Offline EJS readiness checks; never contact a video service."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from js_runtime import WebJavaScriptRuntime
from web.backend.ejs_probe import probe_ejs_runtime


class EjsProbeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = WebJavaScriptRuntime("node", "private/runtime/path", "22.0.0")
        self.provider = SimpleNamespace(
            JS_RUNTIME_NAME="node", is_available=lambda: True,
            _lib_script=object(), _core_script=object(),
            _run_js_runtime=lambda source: '{"web_runtime_probe":2}',
        )
        self.ydl = MagicMock()
        self.factory = MagicMock(return_value=self.ydl)
        self.ydl.__enter__.return_value = self.ydl
        self.director = SimpleNamespace(providers={"Node": self.provider})

    def probe(self):
        with patch("web.backend.ejs_probe._yt_dlp_api", return_value=(
                self.factory, lambda ydl: object(), lambda ie: self.director)):
            return probe_ejs_runtime(self.runtime)

    def test_assets_and_execution_are_distinct_from_real_challenge(self):
        result = self.probe()
        self.assertTrue(result["installed_solver_valid"])
        self.assertTrue(result["provider_available"])
        self.assertTrue(result["runtime_executes"])
        self.assertFalse(result["actual_challenge_solved"])
        options = self.factory.call_args.args[0]
        self.assertEqual(options["remote_components"], [])
        self.assertFalse(options["cachedir"])
        self.assertEqual(options["js_runtimes"], self.runtime.ytdlp_options["js_runtimes"])
        with self.assertRaises(RuntimeError):
            self.ydl.urlopen("https://example.invalid")

    def test_missing_provider(self):
        self.director.providers = {}
        result = self.probe()
        self.assertEqual(result["code"], "provider_unavailable")
        self.assertFalse(result["runtime_executes"])

    def test_asset_failure_does_not_expose_exception(self):
        class BrokenAssets:
            JS_RUNTIME_NAME = "node"
            def is_available(self):
                return True
            @property
            def _lib_script(self):
                raise RuntimeError("private/runtime/path secret")
        self.director.providers = {"Node": BrokenAssets()}
        result = self.probe()
        self.assertEqual(result["code"], "solver_assets_unavailable")
        self.assertNotIn("private", str(result))

    def test_runtime_failure_does_not_expose_exception(self):
        self.provider._run_js_runtime = MagicMock(side_effect=RuntimeError("private/runtime/path"))
        result = self.probe()
        self.assertEqual(result["code"], "runtime_execution_failed")
        self.assertTrue(result["installed_solver_valid"])
        self.assertFalse(result["runtime_executes"])

    def test_missing_yt_dlp_and_api_drift_fail_closed(self):
        with patch("web.backend.ejs_probe._yt_dlp_api", side_effect=ImportError("private")):
            result = probe_ejs_runtime(self.runtime)
        self.assertEqual(result["code"], "probe_unavailable")
        self.assertNotIn("private", str(result))


if __name__ == "__main__":
    unittest.main()
