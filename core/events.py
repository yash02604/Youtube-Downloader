"""Plain, serializable events adapted from the existing engine callbacks.

Subscribers run on the notifying worker and must return quickly. UI adapters
must enqueue work; they must never touch widgets from a subscriber.
"""
from dataclasses import asdict, dataclass
import logging
import threading


@dataclass(frozen=True)
class DownloadEvent:
    download_id: str
    kind: str
    status: str = ''
    downloaded_bytes: int = 0
    total_bytes: int = 0

    def to_dict(self):
        return asdict(self)


class DownloadEvents:
    def __init__(self, manager):
        self._lock = threading.Lock()
        self._subscribers = []
        manager.register_progress_callback(self._progress)
        manager.register_status_callback(self._status)

    def subscribe(self, callback):
        with self._lock:
            self._subscribers.append(callback)
        def unsubscribe():
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe

    def _publish(self, event):
        with self._lock:
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                logging.getLogger(__name__).exception('Download event subscriber failed')

    def _progress(self, download_id, downloaded, total):
        self._publish(DownloadEvent(download_id, 'progress', downloaded_bytes=downloaded, total_bytes=total))

    def _status(self, download_id, status):
        names = {'Error': 'failed', 'Downloading': 'download_started', 'Fetching info': 'metadata_started'}
        self._publish(DownloadEvent(download_id, names.get(status, status.lower().replace(' ', '_')), status=status))
