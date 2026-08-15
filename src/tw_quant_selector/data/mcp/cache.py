"""TTL + LRU 記憶體快取層。

對應 tw-quant-mcp 的 L1 (in-memory, Ristretto) 提供本機一層快取：
- ``realtime`` 類請求：TTL 預設 5 秒（盤中即時資料）
- ``history`` 類請求：TTL 預設 60 秒（盤後資料）
- ``indicators`` 類請求：TTL 預設 60 秒
- ``market`` 類請求：TTL 預設 60 秒

實作 thread-safe 的 OrderedDict，容量以 ``max_entries`` 控制（LRU 淘汰）。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """簡單 TTL + LRU 快取。"""

    def __init__(self, max_entries: int = 1024, default_ttl: float = 60.0):
        self._max = max_entries
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[T]:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            exp, value = entry
            if exp > 0 and exp < now:
                # expired
                self._store.pop(key, None)
                self.misses += 1
                return None
            # LRU touch
            self._store.move_to_end(key)
            self.hits += 1
            return value

    def set(self, key: str, value: T, ttl: Optional[float] = None) -> None:
        ttl = ttl if ttl is not None else self._default_ttl
        exp = time.monotonic() + ttl if ttl > 0 else 0
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (exp, value)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    def invalidate(self, prefix: str = "") -> int:
        """清除所有以 ``prefix`` 開頭的快取；空字串 = 全部清除。"""
        with self._lock:
            if not prefix:
                n = len(self._store)
                self._store.clear()
                return n
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                self._store.pop(k, None)
            return len(keys)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "size": len(self._store),
                "max": self._max,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 4) if total else 0.0,
            }
