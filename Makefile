# tw-quant-selector Makefile
# 提供一鍵編譯 / 部署 / 測試的入口（T148）

# ──────────────── 路徑與環境 ────────────────
PROJECT_ROOT := $(shell pwd)
ENV_FILE     ?= .env
COMPOSE      := docker compose --env-file $(ENV_FILE)

# ──────────────── 預設目標 ────────────────
.DEFAULT_GOAL := help

help:        ## 顯示可用指令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ──────────────── 編譯 ────────────────
build:       ## 編譯前端 (React/TS) 與後端 (Python/FastAPI)
	@echo "==> docker compose build (含 submodule 偵測)"
	bash scripts/docker_build.sh

build-mcp:   ## 編譯 tw-quant-mcp Go binary（Dockerfile 第二階段會自動做；本機版用 Go toolchain）
	@if [ -d "tw-quant-mcp" ]; then \
		echo "==> 編譯 ./tw-quant-mcp (Go)"; \
		cd tw-quant-mcp && CGO_ENABLED=0 go build -ldflags "-X main.version=local" -o ../tw-quant-mcp ./cmd/mcp-server; \
	else \
		echo "WARN: ./tw-quant-mcp 不存在；改採 docker pull 官方鏡像流程，請走 docker compose up"; \
	fi

pull-mcp:    ## 從 registry 拉取 tw-quant-mcp 映像（若改用鏡像模式）
	docker pull tw-quant-mcp:latest || true

# ──────────────── 執行 ────────────────
up:          ## 啟動 docker-compose（app + postgres；frontend 與 scheduler 視需要）
	$(COMPOSE) up -d

up-all:      ## 啟動全部服務（含 frontend 與 scheduler profile）
	$(COMPOSE) --profile scheduler up -d

down:        ## 停止 docker-compose
	$(COMPOSE) down

logs:        ## 查看容器日誌
	$(COMPOSE) logs -f --tail=200

ps:          ## 顯示容器狀態
	$(COMPOSE) ps

restart:     ## 重啟 app 容器
	$(COMPOSE) restart app

# ──────────────── 測試 ────────────────
test:        ## 執行 pytest 全套測試
	pytest tests/ -v --tb=short

test-mcp:    ## 僅跑 MCP 相關測試
	pytest tests/test_mcp_client.py tests/test_mcp_config.py \
	       tests/test_mcp_realtime_adapter.py tests/test_mcp_status_endpoint.py \
	       tests/test_api.py::TestPortfolioExportImport -v --tb=short

integration-test: ## 啟動 MCP 整合測試（需先 docker compose up -d postgres）
	MCP_TRANSPORT=stdio pytest tests/ -v -k "mcp" --tb=short

coverage:    ## 產生覆蓋率報告
	pytest tests/ --cov=tw_quant_selector --cov-report=term-missing --cov-report=html

# ──────────────── 維護 ────────────────
clean:       ## 清理測試暫存與 cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage /tmp/test_tw_quant_api.duckdb 2>/dev/null || true

lint:        ## 執行 ruff lint
	ruff check src/ tests/ || true

.PHONY: help build build-mcp pull-mcp up up-all down logs ps restart test test-mcp integration-test coverage clean lint
