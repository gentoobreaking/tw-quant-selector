import asyncio
import json
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from tw_quant_selector.api.websocket_manager import QuoteWebSocketManager, QUOTE_UPDATE


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


if __name__ == "__main__":
    unittest.main()
