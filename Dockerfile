# === Stage 1: Build Frontend ===
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# === Stage 2: Python API + Serve Static ===
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

# 更新 CA 憑證，確保 yfinance HTTPS 正常
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir --force-reinstall urllib3 requests certifi

WORKDIR /app

COPY pyproject.toml README.md log_config.json ./
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN pip install --no-cache-dir -e .

COPY --from=frontend-builder /app/frontend/dist/ ./frontend/dist/

EXPOSE 8000

CMD ["uvicorn", "tw_quant_selector.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--log-config", "/app/log_config.json"]
