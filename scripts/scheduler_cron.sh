#!/usr/bin/env bash
# TW Quant Scheduler cron script
# 週一至五 17:30 執行每日 pipeline（包裝 run_pipeline_with_retry.sh 取得限流自動重試）
# Log: ~/logs/scheduler_cron.log

set -e

LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/scheduler_cron.log"

cd /Users/claw/Projects/tw-quant-selector

echo "========================================" >> "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') Scheduler cron started" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

FINMIND_TOKEN="${FINMIND_TOKEN:-}"
if [ -z "$FINMIND_TOKEN" ]; then
    if [ -f "$HOME/.env_finmind" ]; then
        source "$HOME/.env_finmind"
    fi
fi

if [ -z "$FINMIND_TOKEN" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR: FINMIND_TOKEN not set" >> "$LOG_FILE"
    exit 1
fi

source .venv/bin/activate 2>/dev/null || true

FINMIND_TOKEN="$FINMIND_TOKEN" \
    bash scripts/run_pipeline_with_retry.sh \
    >> "$LOG_FILE" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') Scheduler cron finished" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
