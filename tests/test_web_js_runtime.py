"""Offline checks for explicit, quiet web JavaScript runtime selection."""

import logging
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from js_runtime import DenoRuntime, discover_deno, discover_web_runtime


def executable(root, name):
    path = root / name
    path.touch()
    return path


def result(version):
    return subprocess.CompletedProcess([], 0, stdout=version)


class WebRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {
            "LOCALAPPDATA": str(self.root / "localappdata"),
        }))
        for target, value in [
            ("js_runtime.Path.home", self.root / "home"),
            ("js_runtime.FormatSelector._registered_windows_path", ""),
            ("js_runtime.shutil.which", None),
        ]:
            self.stack.enter_context(patch(target, return_value=value))
        self.stack.enter_context(patch("js_runtime.sys.executable", str(self.root / "python.exe")))

    def test_web_prefers_supported_deno(self):
        isolated = self.root
        deno = executable(isolated, "deno.exe")
        with patch("js_runtime.subprocess.run", return_value=result("deno 2.3.0\n")), \
                patch("js_runtime.logger.info") as log:
            runtime = discover_web_runtime(str(deno), application_dir=isolated / "app")
        assert (runtime.name, runtime.version) == ("deno", "2.3.0")
        assert runtime.ytdlp_options == {"js_runtimes": {"deno": {"path": str(deno.resolve())}}}
        log.assert_not_called()

    def test_web_discovers_deno_python_wheel_binary(self):
        deno = executable(self.root, 'wheel-deno.exe')
        package = SimpleNamespace(find_deno_bin=lambda: str(deno))
        with patch.dict('sys.modules', {'deno': package}), \
                patch('js_runtime.subprocess.run', return_value=result('deno 2.9.8\n')):
            runtime = discover_web_runtime(application_dir=self.root / 'app')
        self.assertEqual((runtime.name, runtime.version), ('deno', '2.9.8'))


    def test_web_uses_supported_node(self):
        isolated = self.root
        version = "v22.0.0"
        node = executable(isolated, "node.exe")
        with patch("js_runtime.subprocess.run", return_value=result(version)):
            runtime = discover_web_runtime(application_dir=isolated / "app", configured_node_path=str(node))
        assert (runtime.name, runtime.version, runtime.path) == ("node", version[1:], str(node.resolve()))
        assert runtime.ytdlp_options == {"js_runtimes": {"node": {"path": str(node.resolve())}}}


    def test_web_rejects_unsupported_node(self):
        isolated = self.root
        version = "v21.9.0"
        node = executable(isolated, "node.exe")
        with patch("js_runtime.subprocess.run", return_value=result(version)):
            assert discover_web_runtime(application_dir=isolated / "app", configured_node_path=str(node)) is None


    def test_web_missing_runtime(self):
        isolated = self.root
        assert discover_web_runtime(application_dir=isolated / "app") is None


    def test_desktop_discovery_preserves_type_options_and_logging(self):
        isolated = self.root
        deno = executable(isolated, "desktop-deno.exe")
        with patch("js_runtime.subprocess.run", return_value=result("deno 2.3.1\n")), \
                self.assertLogs("js_runtime", logging.INFO) as logs:
            runtime = discover_deno(str(deno), application_dir=isolated / "app")
        assert runtime == DenoRuntime(str(deno.resolve()), "2.3.1")
        assert runtime.ytdlp_options == {"js_runtimes": {"deno": {"path": runtime.path}}}
        assert runtime.path in " ".join(logs.output)

    def test_rejects_invalid_or_prerelease_node_version(self):
        node = executable(self.root, "node.exe")
        for version in ("not node", "v22.0.0-pre", "v20.19.0"):
            with self.subTest(version=version), patch(
                    "js_runtime.subprocess.run", return_value=result(version)):
                self.assertIsNone(discover_web_runtime(
                    application_dir=self.root / "app", configured_node_path=str(node)))

    def test_unsupported_deno_falls_back_to_node_without_paths_in_logs(self):
        deno = executable(self.root, "deno.exe")
        node = executable(self.root, "node.exe")
        with patch("js_runtime.subprocess.run", side_effect=[
                result("deno 2.2.9\n"), result("v24.0.0\n")]), \
                patch("js_runtime.logger.info") as log:
            runtime = discover_web_runtime(
                str(deno), self.root / "app", configured_node_path=str(node))
        self.assertEqual(runtime.name, "node")
        log.assert_not_called()

    def test_node_on_path_and_timeout(self):
        node = executable(self.root, "node.exe")
        with patch("js_runtime.shutil.which", side_effect=lambda name, **kwargs: str(node) if name == "node" else None):
            with patch("js_runtime.subprocess.run", return_value=result("v22.3.1\n")):
                self.assertEqual(discover_web_runtime(application_dir=self.root / "app").name, "node")
            with patch("js_runtime.subprocess.run", side_effect=subprocess.TimeoutExpired("node", 5)):
                self.assertIsNone(discover_web_runtime(application_dir=self.root / "app"))


if __name__ == "__main__":
    unittest.main()
