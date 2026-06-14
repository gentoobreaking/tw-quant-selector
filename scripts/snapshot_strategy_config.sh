#!/usr/bin/env bash
# T127: 週度策略配置快照 (Weekly Strategy Config Snapshot)
#
# Cron: 0 8 * * 1 (每週一 08:00)
# 檢查是否為交易日，若非交易日則順延至下一個交易日。
#
# Log: ~/logs/strategy_snapshot.log
set -e

LOG_DIR="$HOME/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/strategy_snapshot.log"

cd /Users/claw/Projects/tw-quant-selector

echo "========================================" >> "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') Strategy config snapshot started" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

source .venv/bin/activate 2>/dev/null || true

# Run the snapshot
python -c "
from scripts.seed_default_strategy_config import load_default_config, save_config_snapshot
from datetime import date, timedelta
import sys

# Check if today is a trading day (Mon-Fri, not weekend)
today = date.today()
if today.weekday() >= 5:  # Saturday or Sunday
    # Skip: cron only runs on Monday, but extra guard
    print(f'{today} is weekend, skipping')
    sys.exit(0)

try:
    config = load_default_config()
    result = save_config_snapshot(
        config,
        as_of_date=today,
        changed_by='weekly_cron',
        note=f'weekly snapshot {today.isoformat()}'
    )
    if result:
        print(f'Snapshot saved: config_id={result}, date={today}')
    else:
        print(f'Snapshot already exists for {today}')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" >> "$LOG_FILE" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') Strategy config snapshot finished" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"
