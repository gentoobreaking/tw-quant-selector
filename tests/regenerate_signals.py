#!/usr/bin/env python3
"""重新生成信号数据（修复策略排名bug后）"""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent / "src"))

from tw_quant_selector.data.database import Database
from tw_quant_selector.strategies.combiner import compute_composite_scores, DEFAULT_WEIGHTS
import structlog

# 配置日志
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
)

db = Database(read_only=False)
db.init_db()

def regenerate_for_date(target_date: date):
    """重新生成指定日期的信号"""
    print(f"\n🔄 重新生成 {target_date} 的信号...")
    
    try:
        result = compute_composite_scores(
            db, 
            as_of_date=target_date,
            weights=DEFAULT_WEIGHTS,
            top_n_stocks=50,  # 多生成一些，前端可以LIMIT
            top_n_etfs=10
        )
        print(f"✅ 成功！生成了 {result['total_candidates']} 支股票的信号")
        return True
    except Exception as e:
        print(f"❌ 失败：{e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("=" * 60)
    print("修复策略排名bug - 重新生成信号")
    print("=" * 60)
    
    # 获取数据库中最新的几个日期
    rows = db.execute("""
        SELECT DISTINCT signal_date 
        FROM signals 
        ORDER BY signal_date DESC 
        LIMIT 5
    """).fetchall()
    
    if not rows:
        print("❌ 数据库中没有信号数据！")
        return
    
    print(f"\n找到 {len(rows)} 个日期的信号：")
    for r in rows:
        print(f"  - {r[0]}")
    
    # 重新生成最新日期的信号
    latest_date = date.fromisoformat(str(rows[0][0]))
    
    # 先删除旧数据
    print(f"\n🗑️  删除 {latest_date} 的旧信号数据...")
    with db.connection(read_only=False) as conn:
        conn.execute("DELETE FROM signals WHERE signal_date = ?", [latest_date])
        conn.commit()
    print("✅ 删除完成")
    
    # 重新生成
    success = regenerate_for_date(latest_date)
    
    if success:
        # 验证
        print("\n🔍 验证修复效果...")
        strategies = ['composite', 'momentum', 'value', 'quality', 'growth']
        
        for s in strategies:
            rows = db.execute("""
                SELECT stock_id, rank, score
                FROM signals
                WHERE signal_date = ? AND strategy = ?
                ORDER BY rank
                LIMIT 5
            """, [latest_date, s]).fetchall()
            
            stocks = [f"{r[0]}({r[1]})" for r in rows]
            print(f"  {s:12} TOP5: {stocks}")
        
        print("\n✅ 完成！现在重启后端服务器，然后刷新前端页面")
        print("   前端会自动调用 /api/v1/signals/{date}?strategy={选中的策略}")
    else:
        print("\n❌ 重新生成失败，请检查错误信息")

if __name__ == "__main__":
    main()
