"""tw-quant-mcp client 封裝 (T144).

提供連接 tw-quant-mcp (Go 實作) 的 Python 客戶端，內建：
- stdio / streamable-http transport 切換
- 指數退避重試
- 熔斷器（避免上游持續故障）
- 單行程 Single-flight（避免同一股票並發瀑布）
- TTL 快取層 (記憶體內 LRU)
- 統一介面 ``Quote`` / ``PriceHistory`` / ``TechIndicators``
"""

from .client import (
    MCPClient,
    MCPClientConfig,
    MCPConnectionError,
    MCPTimeoutError,
    MCPToolError,
    CircuitOpenError,
)
from .models import (
    Quote,
    PriceHistory,
    TechIndicators,
    MarketSummary,
    InstitutionalFlow,
)
from .cache import TTLCache
from .circuit import CircuitBreaker, CircuitState

__all__ = [
    "MCPClient",
    "MCPClientConfig",
    "MCPConnectionError",
    "MCPTimeoutError",
    "MCPToolError",
    "CircuitOpenError",
    "Quote",
    "PriceHistory",
    "TechIndicators",
    "MarketSummary",
    "InstitutionalFlow",
    "TTLCache",
    "CircuitBreaker",
    "CircuitState",
]
