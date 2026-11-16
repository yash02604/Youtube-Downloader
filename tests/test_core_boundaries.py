import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.models import Download, DownloadJob
from core.persistence import DownloadPersistence
from core.manager import create_manager


class ModelTests(unittest.TestCase):
    def test_domain_roundtrip_preserves_legacy_schema(self):
        job = DownloadJob('https://example.com/file', '.', 'job')
        job.selected_video_format = {'format_id': '401'}
        job.execution_generation = 4
        record = json.loads(json.dumps(job.to_dict()))
        self.assertEqual(DownloadJob.from_dict(record).to_dict(), record)
        self.assertEqual(job.status, 'Queued')
        self.assertNotIn('pause_event', record)
        self.assertFalse(hasattr(job, 'pause_event'))
        self.assertTrue(hasattr(Download(job.url, '.', 'runtime'), 'pause_event'))

    def test_invalid_domain_input_is_rejected(self):
        for value in (None, [], {}, {'id': 3, 'url': 'https://example.com', 'destination_dir': '.'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DownloadJob.from_dict(value)


class PersistenceTests(unittest.TestCase):
    def test_atomic_save_load_and_preserve_original_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            DownloadPersistence._write_json_atomic(path, [{'id': 'old'}])
            with patch('core.persistence.os.replace', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    DownloadPersistence._write_json_atomic(path, [{'id': 'new'}])
            self.assertEqual(json.loads(path.read_text()), [{'id': 'old'}])
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_history_and_legacy_state_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, history = root / 'state.json', root / 'history.json'
            record = DownloadJob('https://example.com/file', directory, 'job').to_dict()
            record['status'] = 'Downloading'
            DownloadPersistence._write_json_atomic(state, [record])
            DownloadPersistence._write_json_atomic(history, [{'id': 'finished', 'status': 'Completed'}])
            manager, events = create_manager(state_file=state, history_file=history)
            self.assertEqual(manager.get_download_info('job')['status'], 'Interrupted')
            self.assertEqual(manager.history[0]['id'], 'finished')
            manager._save_history()
            self.assertEqual(json.loads(history.read_text())[0]['id'], 'finished')
            received = []
            events.subscribe(received.append)
            manager.downloads['job'].error_category = 'NETWORK_ERROR'
            manager.downloads['job'].error_message = 'test failure'
            manager._notify_status_change('job', 'Error')
            payload = received[-1].to_dict()
            self.assertEqual(payload['event_type'], 'failed')
            self.assertEqual(payload['error_category'], 'NETWORK_ERROR')
            json.dumps(payload, allow_nan=False)

    def test_corrupt_json_and_migration_preserve_source_and_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'legacy.json', root / 'state.json'
            source.write_text('[{"id": "old"}]')
            DownloadPersistence._migrate_legacy_json(source, target, list)
            self.assertEqual(json.loads(target.read_text()), [{'id': 'old'}])
            self.assertTrue(source.exists())
            source.write_text('[]')
            DownloadPersistence._migrate_legacy_json(source, target, list)
            self.assertEqual(json.loads(target.read_text()), [{'id': 'old'}])
            target.write_text('{corrupt')
            manager, _ = create_manager(state_file=target, history_file=target)
            self.assertEqual(manager.get_all_downloads(), [])

    def test_core_import_does_not_load_ui_frameworks(self):
        result = subprocess.run([sys.executable, '-c', "import core.manager,sys; assert 'tkinter' not in sys.modules; assert 'fastapi' not in sys.modules"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
