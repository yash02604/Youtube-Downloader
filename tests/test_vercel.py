"""Offline deployment boundary checks; never contact a media provider."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except ImportError:
    raise unittest.SkipTest('Install web/backend/requirements-test.txt for Vercel adapter tests')
from web.backend.settings import Settings


class VercelTests(unittest.TestCase):
    def test_vercel_settings_use_writable_storage_and_exact_hosts(self):
        with patch.dict(os.environ, {'VERCEL': '1',
                'VERCEL_URL': 'preview.vercel.app',
                'VERCEL_PROJECT_PRODUCTION_URL': 'downloader.example',
                'VERCEL_BRANCH_URL': 'branch.vercel.app'}, clear=True):
            settings = Settings.from_env()
        self.assertTrue(settings.download_root.is_relative_to(Path(tempfile.gettempdir())))
        self.assertTrue(settings.state_root.is_relative_to(Path(tempfile.gettempdir())))
        self.assertIn('preview.vercel.app', settings.allowed_hosts)
        self.assertIn('https://downloader.example', settings.frontend_origins)
        self.assertIn('https://branch.vercel.app', settings.frontend_origins)
        self.assertNotIn('*', settings.allowed_hosts)

    def test_adapter_import_does_not_start_workers_or_desktop(self):
        result = subprocess.run([sys.executable, '-c', """
from unittest.mock import patch
import sys
with patch('core.service.DownloadService', side_effect=AssertionError('worker startup')):
    from web.backend.vercel_app import app
    assert any(route.path == '/api/health' for route in app.routes)
assert 'tkinter' not in sys.modules
assert 'main' not in sys.modules
"""], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_health_with_vercel_host_and_same_origin(self):
        with patch.dict(os.environ, {'VERCEL': '1', 'VERCEL_URL': 'preview.vercel.app'}):
            module = importlib.import_module('web.backend.vercel_app')
            module = importlib.reload(module)
        # Exercise normal lifespan startup/shutdown without submitting jobs.
        with TestClient(module.app, base_url='https://preview.vercel.app') as client:
            response = client.get('/api/health', headers={'Origin': 'https://preview.vercel.app'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['status'], 'ok')
            self.assertEqual(client.get('/api/health', headers={'Origin': 'https://evil.example'}).status_code, 403)
            self.assertEqual(client.get('/api/health', headers={'Host': 'evil.example'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
