import json
import threading
import unittest
from unittest.mock import Mock
from core.events import DownloadEvents


class DownloadEventTests(unittest.TestCase):
    def test_worker_events_are_serializable_and_unsubscribe(self):
        manager = Mock()
        events = DownloadEvents(manager)
        received = []
        unsubscribe = events.subscribe(received.append)
        progress = manager.register_progress_callback.call_args.args[0]
        worker = threading.Thread(target=progress, args=('job', 10, 100))
        worker.start()
        worker.join()
        self.assertEqual(json.loads(json.dumps(received[0].to_dict()))['downloaded_bytes'], 10)
        unsubscribe()
        progress('job', 20, 100)
        self.assertEqual(len(received), 1)

    def test_terminal_status_is_delivered(self):
        manager = Mock()
        events = DownloadEvents(manager)
        callback = Mock()
        events.subscribe(callback)
        manager.register_status_callback.call_args.args[0]('job', 'Completed')
        self.assertEqual(callback.call_args.args[0].kind, 'completed')

    def test_lifecycle_mapping_and_progress_fields(self):
        manager = Mock()
        manager.get_event_snapshot.return_value = {'stage': 'Merging', 'speed': 42, 'eta': 2}
        events = DownloadEvents(manager)
        received = []
        events.subscribe(received.append)
        statuses = {'Queued': 'queued', 'Fetching info': 'metadata_started',
                    'Metadata ready': 'metadata_ready', 'Downloading': 'download_started',
                    'Pausing': 'pausing', 'Paused': 'paused', 'Resumed': 'resumed',
                    'Cancelling': 'cancelling', 'Cancelled': 'cancelled',
                    'Processing': 'processing', 'Merging': 'merging', 'Converting': 'converting',
                    'Completed': 'completed', 'Error': 'failed', 'Interrupted': 'interrupted'}
        status_callback = manager.register_status_callback.call_args.args[0]
        for status, expected in statuses.items():
            status_callback('job', status)
            self.assertEqual(received[-1].event_type, expected)
        manager.register_progress_callback.call_args.args[0]('job', 25, 100)
        payload = received[-1].to_dict()
        self.assertEqual(payload['progress'], 25)
        self.assertEqual(payload['speed'], 42)
        self.assertEqual(payload['stage'], 'Merging')
        self.assertIsInstance(payload['timestamp'], float)
