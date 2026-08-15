# === Stage 1: Build Frontend ===
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# === Stage 2: Build tw-quant-mcp ===
FROM golang:1.26.6-alpine3.24 AS mcp-builder

WORKDIR /app/tw-quant-mcp
COPY tw-quant-mcp/go.mod tw-quant-mcp/go.sum ./
RUN go mod download
COPY tw-quant-mcp/ ./
RUN CGO_ENABLED=0 go build -ldflags "-X main.version=docker" -o /tw-quant-mcp ./cmd/mcp-server

# === Stage 3: Python API + Serve Static ===
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# 更新 CA 憑證，確保 yfinance HTTPS 正常
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --force-reinstall urllib3 requests certifi && \
    pip install --no-cache-dir mcp

WORKDIR /app

# MCP env defaults (T143/T144)
ENV MCP_TRANSPORT=stdio \
    MCP_BINARY_PATH=/app/tw-quant-mcp \
    MCP_HTTP_ADDR=127.0.0.1:8787 \
    DATA_DIR=/data/mcp-cache \
    TW_USE_MCP=1 \
    MCP_ENRICH_EXPORT=1

COPY pyproject.toml README.md log_config.json ./
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e .

COPY --from=frontend-builder /app/frontend/dist/ ./frontend/dist/
COPY --from=mcp-builder /tw-quant-mcp /app/tw-quant-mcp

EXPOSE 5172

CMD ["uvicorn", "tw_quant_selector.api.app:app", "--host", "0.0.0.0", "--port", "5172", "--no-access-log", "--log-config", "/app/log_config.json"]