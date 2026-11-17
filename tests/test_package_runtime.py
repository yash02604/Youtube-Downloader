import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from web.backend.package_runtime import package_runtime


class RuntimePackagingTests(unittest.TestCase):
    def test_generated_bundle_contains_exact_wheel_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'wheel' / 'bin' / 'deno'
            source.parent.mkdir(parents=True)
            source.write_bytes(b'controlled runtime fixture')
            target = root / 'app' / '_runtime' / 'deno'
            with patch.dict('sys.modules', {'deno': SimpleNamespace(find_deno_bin=lambda: str(source))}):
                self.assertEqual(package_runtime(target), target)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            if os.name != 'nt':
                self.assertTrue(target.stat().st_mode & 0o111)

    def test_missing_wheel_binary_fails_build(self):
        with patch.dict('sys.modules', {'deno': SimpleNamespace(find_deno_bin=lambda: 'absent-deno-binary')}):
            with self.assertRaisesRegex(RuntimeError, 'unavailable at build time'):
                package_runtime()
