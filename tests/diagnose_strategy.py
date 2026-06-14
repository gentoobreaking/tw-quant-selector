#!/usr/bin/env python3
"""诊断策略选择问题"""
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

from tw_quant_selector.data.database import Database

db = Database(read_only=True)
db.init_db()

print("=== 诊断 1: 检查 signals 表有哪些策略 ===")
rows = db.execute("""
    SELECT strategy, COUNT(*) as cnt, MIN(signal_date), MAX(signal_date)
    FROM signals
    GROUP BY strategy
    ORDER BY strategy
""").fetchall()

if not rows:
    print("❌ signals 表没有数据！")
    sys.exit(1)

print(f"找到 {len(rows)} 个策略：")
for r in rows:
    print(f"  - {r[0]}: {r[1]} 条记录, 日期范围 {r[2]} ~ {r[3]}")

print("\n=== 诊断 2: 检查某一天不同策略返回的股票 ===")
# 获取最新的一天
latest = db.execute("SELECT MAX(signal_date) FROM signals").fetchone()[0]
print(f"最新日期: {latest}")

for strategy in ['composite', 'momentum', 'value', 'quality', 'growth']:
    rows = db.execute("""
        SELECT COUNT(*), GROUP_CONCAT(stock_id || '(' || rank || ')', ', ') as top5
        FROM (
            SELECT stock_id, rank
            FROM signals
            WHERE signal_date = ? AND strategy = ?
            ORDER BY rank
            LIMIT 5
        )
    """, [latest, strategy]).fetchall()
    
    if rows and rows[0][0] > 0:
        print(f"  ✅ {strategy}: {rows[0][0]} 条, TOP5: {rows[0][1]}")
    else:
        print(f"  ❌ {strategy}: 无数据")

print("\n=== 诊断 3: 检查 API 端点的SQL逻辑 ===")
print("问题可能在 _get_signals 函数的 LEFT JOIN 逻辑")
print("当前SQL会：")
print("  1. 从 signals 表选择指定 strategy 的数据")
print("  2. LEFT JOIN 其他策略的分数（momentum/value/quality/growth）")
print("  3. 如果这些 JOIN 失败，factor_scores 会为空")
print("  4. 但股票列表应该根据 strategy 不同而不同")
print("\n建议检查：")
print("  - 数据库是否有多个 strategy 的数据")
print("  - _get_signals 的 SQL 是否正确地 WHERE s.strategy = ?")

print("\n=== 诊断 4: 检查前端是否正确传递参数 ===")
print("查看浏览器开发者工具的 Network 标签")
print("确认 API 调用：/api/v1/signals/2025-05-30?strategy=momentum&include_etf=true")
print("如果 strategy 参数正确，问题在后端")
print("如果 strategy 参数始终是 composite，问题在前端")
