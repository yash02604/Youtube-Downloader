"""Framework-independent public lifecycle for the existing single engine."""
import copy
import math
import threading
import time
from .manager import DownloadManager
from .events import DownloadEvents
from .event_bus import EventBus
from .destination_policy import DesktopDestinationPolicy


class DownloadService:
    def __init__(self, engine=None, *, destination_policy=None, **engine_options):
        self.engine = engine if engine is not None else DownloadManager(**engine_options)
        self.destination_policy = destination_policy or DesktopDestinationPolicy()
        self._lock = threading.RLock()
        self._shutdown_lock = threading.Lock()
        self._stopping = False
        self._finished = threading.Event()
        self._shutdown_error = None
        self._bus = EventBus()
        self._events = DownloadEvents(self.engine)
        self._detach = self._events.subscribe(self._bus.publish)

    def _accept(self):
        if self._stopping:
            raise RuntimeError('download service is shutting down')

    def add_download(self, url, download_path='.', **options):
        with self._lock:
            self._accept()
            path = self.destination_policy.resolve(download_path)
            return self.engine.add_download(url, path, **options)

    def get_download(self, download_id):
        with self.engine.lock:
            return copy.deepcopy(self.engine.get_download_info(download_id))

    def list_downloads(self):
        with self.engine.lock:
            return copy.deepcopy(self.engine.get_all_downloads())

    def _command(self, method, download_id):
        with self._lock:
            self._accept()
            return getattr(self.engine, method)(download_id)

    def pause(self, download_id):
        return self._command('pause_download', download_id)

    def resume(self, download_id):
        return self._command('resume_download', download_id)

    def cancel(self, download_id):
        return self._command('cancel_download', download_id)

    def remove(self, download_id):
        return self._command('remove_download', download_id)

    def subscribe(self, capacity=128):
        with self._lock:
            self._accept()
            return self._bus.subscribe(capacity)

    def unsubscribe(self, subscription):
        self._bus.unsubscribe(subscription)

    def shutdown(self, timeout=5):
        """Request resumable stop; return True once workers and cleanup finish.

        False means cleanup continues in one daemon coordinator. No worker is
        forcibly killed. Repeated calls wait on the same completion signal.
        """
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('timeout must be finite and nonnegative')
        deadline = time.monotonic() + timeout
        if not self._shutdown_lock.acquire(timeout=timeout):
            return False
        try:
            if not self._stopping:
                self._stopping = True
                self._shutdown_thread = threading.Thread(target=self._finish_shutdown, name='download-service-shutdown', daemon=True)
                self._shutdown_thread.start()
        finally:
            self._shutdown_lock.release()
        return self._finished.wait(max(0, deadline - time.monotonic())) and self._shutdown_error is None

    def _finish_shutdown(self):
        try:
            with self._lock:
                self.engine.stop_accepting_work()
            while not self.engine.workers_stopped():
                time.sleep(0.02)
            self.engine.finish_shutdown()
        except Exception as exc:
            self._shutdown_error = str(exc)
        finally:
            self._detach()
            self._events.close()
            self._bus.close()
            self._finished.set()

    @property
    def shutdown_error(self):
        return self._shutdown_error
