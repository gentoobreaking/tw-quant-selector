#!/usr/bin/env bash
#
# Pipeline wrapper with rate-limit auto-retry.
#
# 退出碼:
#   0   - 全部成功
#   75  - 限流中斷（已記錄至 /tmp/pipeline_state.json）
#   其他 - 真實錯誤
#
# 使用:
#   FINMIND_TOKEN=xxx ./scripts/run_pipeline_with_retry.sh [DATE] [--datasets ...]

set -e

MAX_RETRIES=10
SLEEP_BASE_SEC=300   # 5 分鐘
SLEEP_MAX_SEC=3600   # 1 小時上限
RETRY_COUNT=0
STATE_FILE="${PIPELINE_STATE_FILE:-/tmp/pipeline_state.json}"

while true; do
    echo ""
    echo "═══════════════════════════════════════════════════════"
    echo "🚀 Pipeline 啟動 (重試次數: $RETRY_COUNT)"
    echo "═══════════════════════════════════════════════════════"

    set +e
    python scripts/run_daily_pipeline.py "$@"
    EXIT_CODE=$?
    set -e

    if [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Pipeline 全部完成"
        rm -f "$STATE_FILE"
        exit 0
    fi

    if [ $EXIT_CODE -ne 75 ]; then
        echo "❌ Pipeline 失敗 (exit=$EXIT_CODE)，非 rate-limit 錯誤，不重試"
        exit $EXIT_CODE
    fi

    # Exit 75 = rate-limit hit
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "❌ 已重試 $RETRY_COUNT 次仍限流，放棄"
        echo "   手動檢查 $STATE_FILE"
        exit 75
    fi

    # 動態 sleep：5 分鐘起，每 1.5x 增長，封頂 1 小時
    SLEEP_SEC=$(( SLEEP_BASE_SEC * (3 ** RETRY_COUNT / 2 ** RETRY_COUNT) ))
    if [ $SLEEP_SEC -gt $SLEEP_MAX_SEC ]; then
        SLEEP_SEC=$SLEEP_MAX_SEC
    fi

    echo "⏳ Rate-limit 中斷，等 $((SLEEP_SEC/60)) 分鐘後自動重試"
    echo "   進度已存於 $STATE_FILE"
    sleep $SLEEP_SEC
    RETRY_COUNT=$((RETRY_COUNT + 1))
done
