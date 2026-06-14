from __future__ import annotations
import json
import queue
import threading
from typing import Any


class EventBus:
    """Thread-safe SSE event bus.

    Sync producers (POST/DELETE handlers) call ``broadcast()``.
    The async SSE generator polls its per-client ``queue.Queue`` via
    ``get_nowait()``.
    """

    def __init__(self):
        self._queues: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=128)
        with self._lock:
            self._queues.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def broadcast(self, event_type: str, data: Any = None) -> None:
        payload = json.dumps({"type": event_type, "data": data})
        with self._lock:
            dead: set[queue.Queue] = set()
            for q in self._queues:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    dead.add(q)
            self._queues -= dead
