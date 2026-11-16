"""Plain, serializable events adapted from the existing engine callbacks.

Subscribers run on the notifying worker and must return quickly. UI adapters
must enqueue work; they must never touch widgets from a subscriber.
"""
from dataclasses import asdict, dataclass, field
import logging
import threading
import time


@dataclass(frozen=True)
class DownloadEvent:
    download_id: str
    kind: str
    status: str = ''
    downloaded_bytes: int = 0
    total_bytes: int = 0
    stage: str | None = None
    filename: str | None = None
    speed: float = 0
    eta: float = 0
    progress: float = 0
    error_category: str | None = None
    error_message: str | None = None
    playlist_title: str | None = None
    playlist_index: int | None = None
    playlist_count: int | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def event_type(self):
        return self.kind

    def to_dict(self):
        return {**asdict(self), 'event_type': self.event_type}


class DownloadEvents:
    def __init__(self, manager):
        self._manager = manager
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
        values = self._snapshot(download_id)
        values.update(downloaded_bytes=downloaded, total_bytes=total)
        values['progress'] = min(100, downloaded / total * 100) if total else 0
        self._publish(DownloadEvent(download_id, 'progress', **values))

    def _snapshot(self, download_id):
        snapshot = getattr(self._manager, 'get_event_snapshot', lambda _id: {})(download_id)
        return snapshot if isinstance(snapshot, dict) else {}

    def _status(self, download_id, status):
        names = {'Error': 'failed', 'Downloading': 'download_started', 'Fetching info': 'metadata_started'}
        values = self._snapshot(download_id)
        values['status'] = status
        self._publish(DownloadEvent(download_id, names.get(status, status.lower().replace(' ', '_')), **values))
