"""Bounded pull-based delivery; publishing never calls external consumers."""
from collections import deque
import logging
from queue import Empty
from threading import Lock


class Subscription:
    def __init__(self, capacity, release):
        self.capacity = capacity
        self._release = release
        self._items = deque()
        self._lock = Lock()
        self.closed = False
        self.dropped = 0
        self.resync_required = False

    def put(self, event):
        with self._lock:
            if self.closed:
                return
            if event.kind == 'progress':
                for old in tuple(self._items):
                    if old.kind == 'progress' and old.download_id == event.download_id:
                        self._items.remove(old)
                        break
                if len(self._items) >= self.capacity:
                    self.dropped += 1
                    return
            elif len(self._items) >= self.capacity:
                progress = next((x for x in self._items if x.kind == 'progress'), None)
                if progress is not None:
                    self._items.remove(progress)
                else:
                    self._items.popleft()
                    self.resync_required = True
                self.dropped += 1
            self._items.append(event)

    def get_nowait(self):
        with self._lock:
            if not self._items:
                raise Empty
            return self._items.popleft()

    def drain(self, consumer, limit=64):
        """Run adapter code on the caller's thread, isolating consumer failures."""
        count = 0
        for _ in range(limit):
            try:
                event = self.get_nowait()
            except Empty:
                break
            try:
                consumer(event)
            except Exception:
                logging.getLogger(__name__).exception('Event consumer failed')
            count += 1
        return count

    def close(self):
        with self._lock:
            self.closed = True
            self._items.clear()
        self._release(self)

    def __len__(self):
        with self._lock:
            return len(self._items)


class EventBus:
    def __init__(self, max_subscribers=64):
        self._lock = Lock()
        self._subscriptions = set()
        self._closed = False
        self.max_subscribers = max_subscribers

    def subscribe(self, capacity=128):
        if not isinstance(capacity, int) or not 1 <= capacity <= 4096:
            raise ValueError('capacity must be between 1 and 4096')
        with self._lock:
            if self._closed:
                raise RuntimeError('event bus is closed')
            if len(self._subscriptions) >= self.max_subscribers:
                raise RuntimeError('subscriber limit reached')
            subscription = Subscription(capacity, self.unsubscribe)
            self._subscriptions.add(subscription)
            return subscription

    def unsubscribe(self, subscription):
        with self._lock:
            self._subscriptions.discard(subscription)
        with subscription._lock:
            subscription.closed = True
            subscription._items.clear()

    def publish(self, event):
        with self._lock:
            subscribers = tuple(self._subscriptions)
        for subscription in subscribers:
            subscription.put(event)

    def close(self):
        with self._lock:
            self._closed = True
            subscribers = tuple(self._subscriptions)
        for subscription in subscribers:
            subscription.close()
