import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from queue import Empty
from unittest.mock import Mock, patch
from core.destination_policy import DesktopDestinationPolicy, RootedDestinationPolicy
from core.event_bus import EventBus
from core.events import DownloadEvent
from core.service import DownloadService


class EventBusTests(unittest.TestCase):
    def test_thousands_of_updates_coalesce_and_terminal_survives(self):
        bus = EventBus()
        slow, fast = bus.subscribe(4), bus.subscribe(4)
        for n in range(5000):
            bus.publish(DownloadEvent('job', 'progress', downloaded_bytes=n))
            fast.get_nowait()
        self.assertEqual(len(slow), 1)
        self.assertEqual(slow.get_nowait().downloaded_bytes, 4999)
        for n in range(4):
            bus.publish(DownloadEvent(str(n), 'progress'))
        bus.publish(DownloadEvent('job', 'completed'))
        self.assertLessEqual(len(slow), 4)
        self.assertIn('completed', [slow.get_nowait().kind for _ in range(len(slow))])

    def test_status_overflow_is_explicit_and_unsubscribe_cleans_up(self):
        bus = EventBus(max_subscribers=1)
        subscription = bus.subscribe(1)
        bus.publish(DownloadEvent('a', 'paused'))
        bus.publish(DownloadEvent('b', 'completed'))
        self.assertTrue(subscription.resync_required)
        self.assertEqual(subscription.dropped, 1)
        self.assertEqual(subscription.get_nowait().kind, 'completed')
        with self.assertRaises(RuntimeError):
            bus.subscribe()
        subscription.close()
        self.assertTrue(subscription.closed)
        self.assertEqual(len(subscription), 0)
        self.assertIsNotNone(bus.subscribe())

    def test_broken_consumer_is_isolated_from_other_consumers(self):
        bus = EventBus()
        a, b = bus.subscribe(), bus.subscribe()
        bus.publish(DownloadEvent('job', 'completed'))
        with self.assertLogs('core.event_bus', level='ERROR'):
            a.drain(Mock(side_effect=RuntimeError('broken')))
        self.assertEqual(b.get_nowait().kind, 'completed')
        bus.close()
        with self.assertRaises(RuntimeError):
            bus.subscribe()

    def test_publish_does_not_wait_for_consumer_code(self):
        bus = EventBus()
        sub = bus.subscribe()
        entered, release = threading.Event(), threading.Event()
        bus.publish(DownloadEvent('job', 'queued'))
        def consumer(_event):
            entered.set()
            release.wait(2)
        thread = threading.Thread(target=lambda: sub.drain(consumer, limit=1))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            bus.publish(DownloadEvent('job', 'completed'))
            self.assertEqual(len(sub), 1)
        finally:
            release.set()
            thread.join(2)


class DestinationTests(unittest.TestCase):
    def test_desktop_and_root_relative_normalization(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = RootedDestinationPolicy(directory)
            self.assertEqual(Path(policy.resolve('videos/./today')), Path(directory).resolve() / 'videos/today')
            self.assertEqual(Path(policy.resolve('videos\\today')), Path(directory).resolve() / 'videos/today')
            self.assertEqual(Path(DesktopDestinationPolicy().resolve(directory)), Path(directory))

    def test_remote_rejects_windows_and_posix_escapes(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = RootedDestinationPolicy(directory)
            for value in ('../x', 'a/../../x', '..\\x', '/tmp', 'C:\\temp', 'D:relative', '\\\\server\\share', '//server/share', 'x:stream', 'folder./x', 'folder /x', 'NUL', 'dir/COM1.txt', '\x00'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    policy.resolve(value)

    def test_symlink_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            link = Path(directory) / 'escape'
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest('symlink creation is unavailable to this Windows account')
            with self.assertRaises(ValueError):
                RootedDestinationPolicy(directory).resolve('escape/file')


class ServiceTests(unittest.TestCase):
    def make_service(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = DownloadService(state_file=Path(temp.name)/'state.json', history_file=Path(temp.name)/'history.json')
        self.addCleanup(lambda: service.shutdown(2))
        service.engine.max_concurrent = 0  # Keep all jobs offline and queued.
        return service, temp.name

    def test_public_api_snapshots_events_and_remove(self):
        service, directory = self.make_service()
        sub = service.subscribe()
        job = service.add_download('https://example.com/file', directory)
        self.assertEqual(service.get_download(job)['status'], 'Queued')
        self.assertTrue(sub.drain(lambda e: json.dumps(e.to_dict())))
        snapshot = service.list_downloads()
        snapshot[0]['optional_media_warnings'].append('mutated')
        self.assertEqual(service.get_download(job)['optional_media_warnings'], [])
        for method, expected in (('pause', 'Paused'), ('resume', 'Queued'), ('cancel', 'Cancelled')):
            getattr(service, method)(job)
            self.assertEqual(service.get_download(job)['status'], expected)
        self.assertTrue(service.remove(job))
        self.assertIsNone(service.get_download(job))
        service.unsubscribe(sub)
        self.assertTrue(sub.closed)

    def test_shutdown_persists_paused_jobs_rejects_jobs_and_detaches(self):
        service, directory = self.make_service()
        job = service.add_download('https://example.com/file', directory)
        self.assertTrue(service.shutdown(2))
        self.assertTrue(service.shutdown(0))
        self.assertEqual(service.get_download(job)['status'], 'Paused')
        self.assertEqual(json.loads(service.engine.state_file.read_text())[0]['status'], 'Paused')
        self.assertEqual(service.engine.progress_callbacks, [])
        self.assertEqual(service.engine.status_callbacks, [])
        for action in (lambda: service.add_download('https://example.com', directory), lambda: service.resume(job), lambda: service.subscribe()):
            with self.assertRaises(RuntimeError):
                action()

    def test_shutdown_timeout_is_bounded_while_worker_finishes(self):
        service, directory = self.make_service()
        job_id = service.add_download('https://example.com/file', directory)
        job = service.engine.downloads[job_id]
        release = threading.Event()
        def finish():
            release.wait(3)
            service.engine._transition_download(job_id, 'Paused')
            service.engine._release_active_slot(job_id)
        job.status = 'Downloading'
        job.slot_active = True
        service.engine.active_downloads = 1
        job.worker = threading.Thread(target=finish)
        job.worker.start()
        try:
            start = time.monotonic()
            self.assertFalse(service.shutdown(0.01))
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertNotEqual(service.get_download(job_id)['status'], 'Completed')
        finally:
            release.set()
        self.assertTrue(service.shutdown(2))
        self.assertIsNone(job.worker)
        self.assertEqual(service.get_download(job_id)['status'], 'Paused')

    def test_service_uses_remote_policy_before_engine(self):
        engine = Mock()
        with tempfile.TemporaryDirectory() as directory:
            service = DownloadService(engine, destination_policy=RootedDestinationPolicy(directory))
            with self.assertRaises(ValueError):
                service.add_download('https://example.com', '../escape')
            engine.add_download.assert_not_called()
            service.add_download('https://example.com', 'videos')
            self.assertEqual(engine.add_download.call_args.args[1], str(Path(directory).resolve()/'videos'))
            service.shutdown(1)

    def test_service_import_is_framework_free(self):
        result = subprocess.run([sys.executable, '-c', "import core.service,sys; assert 'tkinter' not in sys.modules; assert 'fastapi' not in sys.modules"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shutdown_admission_stops_while_existing_command_is_blocked(self):
        service, directory = self.make_service()
        entered, release = threading.Event(), threading.Event()
        def admitted_operation():
            with service._lock:
                entered.set()
                release.wait(2)
        worker = threading.Thread(target=admitted_operation)
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertFalse(service.shutdown(0.01))
            self.assertTrue(service._stopping)
        finally:
            release.set()
            worker.join(2)
        self.assertTrue(service.shutdown(2))
        with self.assertRaises(RuntimeError):
            service.add_download('https://example.com', directory)
