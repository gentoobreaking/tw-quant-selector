from __future__ import annotations
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
import structlog

log = structlog.get_logger()

QUOTE_UPDATE = "quote_update"
ALERT_TRIGGERED = "alert_triggered"


class QuoteWebSocketManager:
    """Manages WebSocket connections for realtime quote push."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._last_prices: dict[str, float] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        log.info("ws.connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        log.info("ws.disconnected", total=len(self._connections))

    async def broadcast(self, quote_data: dict[str, Any]) -> None:
        payload = json.dumps({
            "type": QUOTE_UPDATE,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": quote_data,
        })
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast_changed(
        self, quotes: list[dict[str, Any]],
    ) -> None:
        changed: dict[str, Any] = {}
        now_ts = int(time.time())
        for q in quotes:
            sid = q.get("stock_id", "")
            price = q.get("price")
            prev = self._last_prices.get(sid)
            if price is not None and prev is not None and abs(price - prev) < 0.01:
                continue
            if price is not None:
                self._last_prices[sid] = price
            entry: dict[str, Any] = {
                "price": price,
                "change_pct": q.get("change_pct"),
                "pe_realtime": q.get("pe_realtime"),
                "pb_realtime": q.get("pb_realtime"),
                "volume": q.get("volume"),
            }
            changed[sid] = {k: v for k, v in entry.items() if v is not None}
            self._last_prices[sid] = changed[sid].get("price", prev)

        if changed:
            await self.broadcast(changed)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


class AlertWebSocketManager:
    """Manages WebSocket connections for realtime smart alert push."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._history: list[dict[str, Any]] = []
        self._max_history = 200

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.add(ws)
        log.info("alert_ws.connected", total=len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.discard(ws)
        log.info("alert_ws.disconnected", total=len(self._connections))

    async def broadcast_alert(self, alert_data: dict[str, Any]) -> None:
        payload = {
            "type": ALERT_TRIGGERED,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": alert_data,
        }
        self._history.append(payload)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        payload_str = json.dumps(payload)
        dead: set[WebSocket] = set()
        for ws in self._connections:
            try:
                await ws.send_text(payload_str)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self.disconnect(ws)

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._history[-limit:]))

    @property
    def connection_count(self) -> int:
        return len(self._connections)
