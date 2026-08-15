"""Environment-based configuration tests."""

from __future__ import annotations

import os
import unittest

from tw_quant_selector.data.mcp.client import MCPClientConfig


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = MCPClientConfig()
        self.assertEqual(cfg.transport, "stdio")
        self.assertEqual(cfg.http_addr, "127.0.0.1:8787")

    def test_from_env_overrides(self):
        os.environ["MCP_TRANSPORT"] = "streamable-http"
        os.environ["MCP_HTTP_ADDR"] = "10.0.0.1:9000"
        os.environ["MCP_BINARY_PATH"] = "/opt/custom/mcp"
        os.environ["MCP_RETRIES"] = "5"
        try:
            cfg = MCPClientConfig.from_env()
            self.assertEqual(cfg.transport, "streamable-http")
            self.assertEqual(cfg.http_addr, "10.0.0.1:9000")
            self.assertEqual(cfg.binary_path, "/opt/custom/mcp")
            self.assertEqual(cfg.retries, 5)
        finally:
            del os.environ["MCP_TRANSPORT"]
            del os.environ["MCP_HTTP_ADDR"]
            del os.environ["MCP_BINARY_PATH"]
            del os.environ["MCP_RETRIES"]


if __name__ == "__main__":
    unittest.main()
