#!/usr/bin/env bash
# scripts/docker_build.sh — 一鍵 build（含 submodule 偵測）
# 用法：bash scripts/docker_build.sh
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> [1/3] 檢查 git submodule (tw-quant-mcp)"
if [ ! -f "tw-quant-mcp/go.mod" ]; then
    echo "      tw-quant-mcp/go.mod 不存在，嘗試 git submodule update --init --recursive"
    git -c protocol.file.allow=always submodule update --init --recursive
fi

echo "==> [2/3] 檢查 .env"
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "      ✓ 已複製 .env.example → .env"
fi

echo "==> [3/3] docker compose build"
docker compose --env-file .env build

echo "==> 完成"