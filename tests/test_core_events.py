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
