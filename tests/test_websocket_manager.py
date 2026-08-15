import asyncio
import json
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from tw_quant_selector.api.websocket_manager import (
    AlertWebSocketManager,
    QuoteWebSocketManager,
    ALERT_TRIGGERED,
    QUOTE_UPDATE,
)


class TestQuoteWebSocketManager(unittest.TestCase):

    def setUp(self):
        self.manager = QuoteWebSocketManager()

    def test_initial_state(self):
        self.assertEqual(self.manager.connection_count, 0)
        self.assertEqual(len(self.manager._last_prices), 0)

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_connect(self, mock_log):
        ws = AsyncMock()
        asyncio.run(self.manager.connect(ws))
        self.assertEqual(self.manager.connection_count, 1)
        ws.accept.assert_awaited_once()

    def test_disconnect(self):
        ws = MagicMock()
        self.manager._connections.add(ws)
        self.manager.disconnect(ws)
        self.assertEqual(self.manager.connection_count, 0)

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_disconnect_unknown(self, mock_log):
        ws = MagicMock()
        self.manager.disconnect(ws)
        self.assertEqual(self.manager.connection_count, 0)

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_broadcast_sends_to_all(self, mock_log):
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        self.manager._connections.update([ws1, ws2])

        asyncio.run(self.manager.broadcast({"2330": {"price": 895.0}}))

        self.assertEqual(ws1.send_text.call_count, 1)
        self.assertEqual(ws2.send_text.call_count, 1)
        payload = json.loads(ws1.send_text.call_args[0][0])
        self.assertEqual(payload["type"], QUOTE_UPDATE)
        self.assertIn("data", payload)
        self.assertIn("timestamp", payload)

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_broadcast_removes_dead_connections(self, mock_log):
        ws_ok = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_text.side_effect = Exception("Connection lost")
        self.manager._connections.update([ws_ok, ws_dead])

        asyncio.run(self.manager.broadcast({"2330": {"price": 895.0}}))

        self.assertEqual(self.manager.connection_count, 1)
        self.assertIn(ws_ok, self.manager._connections)
        self.assertNotIn(ws_dead, self.manager._connections)

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_broadcast_changed_filters_unchanged(self, mock_log):
        ws = AsyncMock()
        self.manager._connections.add(ws)
        self.manager._last_prices["2330"] = 895.0

        asyncio.run(self.manager.broadcast_changed([
            {"stock_id": "2330", "price": 895.0, "change_pct": 0.0, "volume": 1000},
        ]))

        ws.send_text.assert_not_called()

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_broadcast_changed_includes_changed(self, mock_log):
        ws = AsyncMock()
        self.manager._connections.add(ws)

        asyncio.run(self.manager.broadcast_changed([
            {"stock_id": "2330", "price": 895.0, "change_pct": 2.5, "pe_realtime": 22.1, "pb_realtime": 3.5, "volume": 10000},
        ]))

        self.assertEqual(ws.send_text.call_count, 1)
        payload = json.loads(ws.send_text.call_args[0][0])
        self.assertEqual(payload["type"], QUOTE_UPDATE)
        self.assertIn("2330", payload["data"])

    def test_broadcast_changed_empty_quotes(self):
        result = asyncio.run(self.manager.broadcast_changed([]))
        self.assertIsNone(result)


class TestAlertWebSocketManager(unittest.TestCase):

    def setUp(self):
        self.manager = AlertWebSocketManager()

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_connect(self, mock_log):
        ws = AsyncMock()
        asyncio.run(self.manager.connect(ws))
        self.assertEqual(self.manager.connection_count, 1)
        ws.accept.assert_awaited_once()

    def test_disconnect(self):
        ws = MagicMock()
        self.manager._connections.add(ws)
        self.manager.disconnect(ws)
        self.assertEqual(self.manager.connection_count, 0)

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_broadcast_alert_sends_and_records_history(self, mock_log):
        ws = AsyncMock()
        self.manager._connections.add(ws)

        asyncio.run(self.manager.broadcast_alert({
            "alert_type": "WHALE_MOVE",
            "stock_id": "2330",
            "severity": "CRITICAL",
        }))

        self.assertEqual(ws.send_text.call_count, 1)
        payload = json.loads(ws.send_text.call_args[0][0])
        self.assertEqual(payload["type"], ALERT_TRIGGERED)
        self.assertEqual(payload["data"]["alert_type"], "WHALE_MOVE")
        self.assertEqual(len(self.manager._history), 1)
        self.assertEqual(payload["timestamp"], self.manager._history[0]["timestamp"])

    @patch("tw_quant_selector.api.websocket_manager.log")
    def test_broadcast_alert_removes_dead_connections(self, mock_log):
        ws_ok = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_text.side_effect = Exception("Connection lost")
        self.manager._connections.update([ws_ok, ws_dead])

        asyncio.run(self.manager.broadcast_alert({"alert_type": "VOLUME_SPIKE"}))

        self.assertEqual(self.manager.connection_count, 1)
        self.assertIn(ws_ok, self.manager._connections)
        self.assertNotIn(ws_dead, self.manager._connections)

    def test_get_recent_default_limit(self):
        for i in range(5):
            self.manager._history.append({"type": ALERT_TRIGGERED, "index": i})
        recent = self.manager.get_recent()
        self.assertEqual(len(recent), 5)
        # newest first
        self.assertEqual(recent[0]["index"], 4)
        self.assertEqual(recent[-1]["index"], 0)

    def test_get_recent_respects_limit(self):
        for i in range(10):
            self.manager._history.append({"index": i})
        recent = self.manager.get_recent(limit=3)
        self.assertEqual([r["index"] for r in recent], [9, 8, 7])

    def test_get_recent_limit_greater_than_history(self):
        self.manager._history.append({"index": 0})
        recent = self.manager.get_recent(limit=200)
        self.assertEqual(len(recent), 1)

    def test_history_capped_at_200(self):
        for i in range(250):
            asyncio.run(self.manager.broadcast_alert({"alert_type": "TEST", "n": i}))
        self.assertEqual(len(self.manager._history), 200)
        # Oldest kept entry is the 51st broadcast (index 50)
        self.assertEqual(self.manager._history[0]["data"]["n"], 50)
        self.assertEqual(self.manager._history[-1]["data"]["n"], 249)

    def test_history_keeps_newest_200(self):
        for i in range(250):
            self.manager._history.append({"index": i})
        self.manager._history = self.manager._history[-200:]
        # Oldest kept entry is index 50
        self.assertEqual(self.manager._history[0]["index"], 50)
        self.assertEqual(len(self.manager._history), 200)


if __name__ == "__main__":
    unittest.main()
