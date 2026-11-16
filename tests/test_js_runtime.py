import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from js_runtime import MIN_SUPPORTED_DENO, DenoRuntime, discover_deno


class DenoDiscoveryTests(unittest.TestCase):
    def _version_result(self, version):
        return subprocess.CompletedProcess(
            ["deno", "--version"], 0, stdout=f"deno {version}\nv8 x\ntypescript x\n", stderr=""
        )

    def _isolated_discovery(self, root):
        return (
            patch.dict(os.environ, {"PATH": "", "LOCALAPPDATA": str(root / "localappdata")}),
            patch("js_runtime.Path.home", return_value=root / "home"),
            patch("js_runtime.FormatSelector._registered_windows_path", return_value=""),
        )

    def test_discovers_deno_on_path_and_checks_its_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "deno.exe"
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=str(deno)):
                    with patch("js_runtime.subprocess.run", return_value=self._version_result("2.3.0")) as run:
                        runtime = discover_deno(application_dir=root / "app")

        self.assertEqual(runtime, DenoRuntime(str(deno.resolve()), "2.3.0"))
        run.assert_called_once_with(
            [str(deno), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5,
        )

    def test_discovers_configured_deno_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "deno" / "bin"
            bin_dir.mkdir(parents=True)
            deno = bin_dir / "deno.exe"
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    with patch("js_runtime.subprocess.run", return_value=self._version_result("2.4.1")):
                        runtime = discover_deno(str(bin_dir), application_dir=root / "app")

        self.assertEqual(runtime.path, str(deno.resolve()))
        self.assertEqual(runtime.version, "2.4.1")

    def test_discovers_configured_deno_executable_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "custom" / "deno.exe"
            deno.parent.mkdir(parents=True)
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    with patch("js_runtime.subprocess.run", return_value=self._version_result("2.5.0")):
                        runtime = discover_deno(str(deno), application_dir=root / "app")

        self.assertEqual(runtime.path, str(deno.resolve()))

    def test_reuses_verified_deno_until_executable_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "deno.exe"
            deno.write_bytes(b"first")
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry, patch("js_runtime.shutil.which", return_value=None):
                with patch("js_runtime.subprocess.run", return_value=self._version_result("2.9.8")) as run:
                    first = discover_deno(str(deno), application_dir=root / "app")
                    second = discover_deno(str(deno), application_dir=root / "app")
                    self.assertEqual(first, second)
                    self.assertEqual(run.call_count, 1)
                    deno.write_bytes(b"changed executable")
                    discover_deno(str(deno), application_dir=root / "app")
                    self.assertEqual(run.call_count, 2)

    def test_discovers_bundled_deno_bin_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "app" / "deno" / "bin" / "deno.exe"
            deno.parent.mkdir(parents=True)
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    with patch("js_runtime.subprocess.run", return_value=self._version_result("2.3.1")):
                        runtime = discover_deno(application_dir=root / "app")

        self.assertEqual(runtime.path, str(deno.resolve()))

    def test_discovers_common_user_install_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "home" / ".deno" / "bin" / "deno.exe"
            deno.parent.mkdir(parents=True)
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    with patch("js_runtime.subprocess.run", return_value=self._version_result("2.3.0")):
                        runtime = discover_deno(application_dir=root / "app")

        self.assertEqual(runtime.path, str(deno.resolve()))

    def test_returns_none_when_deno_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    self.assertIsNone(discover_deno(application_dir=root / "app"))

    def test_returns_none_when_executable_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "deno.exe"
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    with patch(
                        "js_runtime.subprocess.run",
                        side_effect=subprocess.CalledProcessError(1, [str(deno), "--version"]),
                    ):
                        self.assertIsNone(discover_deno(str(deno), application_dir=root / "app"))

    def test_rejects_deno_below_yt_dlp_minimum(self):
        self.assertEqual(MIN_SUPPORTED_DENO, (2, 3, 0))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deno = root / "deno.exe"
            deno.touch()
            env, home, registry = self._isolated_discovery(root)
            with env, home, registry:
                with patch("js_runtime.shutil.which", return_value=None):
                    with patch("js_runtime.subprocess.run", return_value=self._version_result("2.2.9")):
                        self.assertIsNone(discover_deno(str(deno), application_dir=root / "app"))

    def test_runtime_supplies_supported_ytdlp_python_api_options(self):
        runtime = DenoRuntime("C:\\tools\\deno.exe", "2.3.0")
        self.assertEqual(
            runtime.ytdlp_options,
            {"js_runtimes": {"deno": {"path": "C:\\tools\\deno.exe"}}},
        )


if __name__ == "__main__":
    unittest.main()
