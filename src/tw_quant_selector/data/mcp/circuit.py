"""Circuit breaker 實作（避免上游持續故障時資源耗盡）。

狀態機：
- CLOSED：正常請求。連續 N 次失敗 → OPEN
- OPEN：直接拒絕所有請求。經 ``reset_timeout`` 秒 → HALF_OPEN
- HALF_OPEN：放行 1 個探測請求。成功 → CLOSED；失敗 → OPEN

設計參考 §5.3，但簡化為單一全域計數（非 per-host），
因為 selector 內 MCP 僅面對 tw-quant-mcp 單一上游。
"""

from __future__ import annotations

import threading
import time
from enum import Enum
from typing import Any, Callable


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Circuit breaker OPEN 狀態下直接拒絕請求。"""

    def __init__(self, message: str = "circuit open"):
        super().__init__(message)
        self.message = message


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        half_open_max_calls: int = 1,
        success_threshold: int = 2,
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes_in_half = 0
        self._opened_at: float = 0.0
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def allow(self) -> bool:
        """是否允許通過。"""
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if time.monotonic() - self._opened_at >= self.reset_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._successes_in_half = 0
                    return True
                return False
            # HALF_OPEN
            if self._half_open_calls < self.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._successes_in_half += 1
                if self._successes_in_half >= self.success_threshold:
                    self._close()
            elif self._state == CircuitState.CLOSED:
                self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._open()
            elif self._state == CircuitState.CLOSED:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._failures = self.failure_threshold
        self._half_open_calls = 0
        self._successes_in_half = 0

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._successes_in_half = 0
        self._half_open_calls = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.value,
                "failures": self._failures,
                "opened_at": self._opened_at,
                "since_open": (
                    round(time.monotonic() - self._opened_at, 2)
                    if self._opened_at
                    else 0
                ),
            }

    def reset(self) -> None:
        with self._lock:
            self._close()
