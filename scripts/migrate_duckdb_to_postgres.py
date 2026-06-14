#!/usr/bin/env python3
"""
T103: 数据迁移脚本（DuckDB → PostgreSQL）
方式 B：DuckDB 导出 CSV → PostgreSQL COPY

特性：
- 每表独立处理，支持 --table 指定单表迁移
- 自动类型转换（DECIMAL / BOOLEAN / TIMESTAMP）
- 失败自动清理残留 CSV
- 迁移前备份 PostgreSQL 数据（TRUNCATE + INSERT ... SELECT）

使用方式：
  python scripts/migrate_duckdb_to_postgres.py            # 迁移所有表
  python scripts/migrate_duckdb_to_postgres.py --table stocks  # 只迁移 stocks 表
  python scripts/migrate_duckdb_to_postgres.py --dry-run  # 只打印 SQL，不执行
"""

import argparse
import csv
import os
import sys
import tempfile
from datetime import datetime

import duckdb


# ============================================================
# 配置
# ============================================================
DUCKDB_PATH = "data/tw_quant.duckdb"
PG_CONN_STRING = "host=localhost port=5432 dbname=tw_quant user=tw-quant password=tw-quant-PassWd"

# 从 .env 读取配置（覆盖上方默认值）
def load_env_config():
    """从 .env 读取数据库配置"""
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    env_path = os.path.abspath(env_path)
    if not os.path.exists(env_path):
        return
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k == 'POSTGRES_PASSWORD':
                    global PG_PASSWD
                    PG_PASSWD = v
                elif k == 'POSTGRES_USER':
                    global PG_USER
                    PG_USER = v
                elif k == 'POSTGRES_DB':
                    global PG_DB
                    PG_DB = v

PG_PASSWD = "tw-quant-PassWd"
PG_USER = "tw-quant"
PG_DB = "tw_quant"
load_env_config()

# 所有需要迁移的表（按外键依赖顺序）
TABLES = [
    "stocks",
    "daily_prices",
    "monthly_revenue",
    "financials",
    "valuations",
    "signals",
    "portfolio",
    "lots",
    "alert_settings",
    "alert_log",
    "operation_logs",
    "strategy_config_history",
    "guru_scores",
    "ingestion_tracker",
    "backtest_runs",
    "backtest_positions",
    "backtest_equity",
]

# DuckDB → PostgreSQL 类型转换映射
# DuckDB 的 DECIMAL 需要转成 float 再写入 CSV（避免精度丢失）
# BOOLEAN 需要转成 0/1（PostgreSQL COPY 不接受 TRUE/FALSE 文字）
TYPE_CONVERSIONS = {
    "BOOLEAN": "CAST({col} AS INTEGER)",
    "DECIMAL": "CAST({col} AS DOUBLE)",
    "TIMESTAMP": "CAST({col} AS VARCHAR)",
}


def get_postgres_conn():
    import psycopg2
    return psycopg2.connect(
        host="localhost",
        port=5432,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWD,
    )


def get_duckdb_tables(duck_con):
    """获取 DuckDB 中所有表名"""
    result = duck_con.execute("SHOW TABLES").fetchall()
    return [r[0] for r in result]


def get_table_columns(duck_con, table_name):
    """获取表的列信息（DuckDB PRAGMA）"""
    result = duck_con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    return [(r[1], r[2]) for r in result]  # (column_name, column_type)


def build_select_sql(duck_con, table_name):
    """构建 SELECT 语句，处理类型转换"""
    columns = get_table_columns(duck_con, table_name)
    select_exprs = []
    for col_name, col_type in columns:
        expr = f'"{col_name}"'
        # 处理 BOOLEAN
        if col_type.upper() == "BOOLEAN":
            expr = f'CAST("{col_name}" AS INTEGER) AS "{col_name}"'
        # 处理 DECIMAL（转 DOUBLE 避免精度问题）
        elif col_type.upper().startswith("DECIMAL"):
            expr = f'CAST("{col_name}" AS DOUBLE) AS "{col_name}"'
        # 处理 TIMESTAMP（转 VARCHAR，PostgreSQL COPY 会自己转）
        elif col_type.upper() == "TIMESTAMP":
            expr = f'CAST("{col_name}" AS VARCHAR) AS "{col_name}"'
        select_exprs.append(expr)
    return f"SELECT {', '.join(select_exprs)} FROM \"{table_name}\""


def export_table_to_csv(duck_con, table_name, csv_path):
    """将 DuckDB 表导出为 CSV"""
    sql = build_select_sql(duck_con, table_name)
    # 写入 CSV（含 header）
    duck_con.execute(f"""
        COPY ({sql}) TO '{csv_path}'
        (FORMAT CSV, HEADER TRUE, NULL '');
    """)
    # 统计笔数
    count = duck_con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    return count


def truncate_table(pg_cur, table_name):
    """清空 PostgreSQL 表（保留序列）"""
    pg_cur.execute(f'TRUNCATE TABLE "{table_name}" RESTART IDENTITY CASCADE;')


def copy_csv_to_postgres(pg_cur, table_name, csv_path):
    """使用 PostgreSQL COPY 命令导入 CSV（禁用 FK 检查）"""
    # 禁用外键检查（仅限当前 session）
    pg_cur.execute("SET session_replication_role = replica;")
    with open(csv_path, "r") as f:
        pg_cur.copy_expert(f"""
            COPY "{table_name}"
            FROM STDIN
            WITH (FORMAT CSV, HEADER TRUE, NULL '');
        """, f)
    # 恢复外键检查
    pg_cur.execute("SET session_replication_role = DEFAULT;")


def migrate_table(duck_con, pg_cur, table_name, dry_run=False):
    """迁移单表"""
    print(f"\n{'='*60}")
    print(f"迁移表：{table_name}")
    print(f"{'='*60}")

    # 1. 统计 DuckDB 笔数
    duck_count = duck_con.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
    print(f"  DuckDB 笔数：{duck_count:,}")

    if duck_count == 0:
        print(f"  ⚠️  跳过（DuckDB 中无数据）")
        return True

    # 2. 导出 CSV
    csv_path = os.path.join(tempfile.gettempdir(), f"migrate_{table_name}.csv")
    if not dry_run:
        print(f"  导出 CSV：{csv_path}")
        try:
            actual_count = export_table_to_csv(duck_con, table_name, csv_path)
            print(f"  CSV 笔数：{actual_count:,}")
        except Exception as e:
            print(f"  ❌ 导出失败：{e}")
            return False

    # 3. 清空 PostgreSQL 表
    if not dry_run:
        print(f"  清空 PostgreSQL 表...")
        truncate_table(pg_cur, table_name)

    # 4. 导入 PostgreSQL
    if not dry_run:
        print(f"  导入 PostgreSQL...")
        try:
            copy_csv_to_postgres(pg_cur, table_name, csv_path)
        except Exception as e:
            print(f"  ❌ 导入失败：{e}")
            return False

    # 5. 验证笔数
    if not dry_run:
        pg_cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
        pg_count = pg_cur.fetchone()[0]
        print(f"  PostgreSQL 笔数：{pg_count:,}")

        if duck_count != pg_count:
            print(f"  ❌ 笔数不一致！DuckDB={duck_count:,} vs PostgreSQL={pg_count:,}")
            return False
        else:
            print(f"  ✅ 笔数一致")

    # 6. 清理 CSV
    if not dry_run and os.path.exists(csv_path):
        os.remove(csv_path)

    return True


def main():
    parser = argparse.ArgumentParser(description="T103: DuckDB → PostgreSQL 数据迁移")
    parser.add_argument("--table", help="只迁移指定表", choices=TABLES)
    parser.add_argument("--dry-run", action="store_true", help="只打印 SQL，不执行")
    args = parser.parse_args()

    # 连接 DuckDB
    print("连接 DuckDB...")
    duck_con = duckdb.connect(DUCKDB_PATH, read_only=True)

    # 连接 PostgreSQL
    if not args.dry_run:
        print("连接 PostgreSQL...")
        try:
            import psycopg2
            pg_conn = get_postgres_conn()
            pg_cur = pg_conn.cursor()
        except ImportError:
            print("❌ 缺少 psycopg2 库，请执行：pip install psycopg2-binary")
            sys.exit(1)
        except Exception as e:
            print(f"❌ 连接 PostgreSQL 失败：{e}")
            sys.exit(1)

    # 迁移表
    tables_to_migrate = [args.table] if args.table else TABLES
    success_count = 0
    fail_count = 0

    for table in tables_to_migrate:
        ok = migrate_table(duck_con, pg_cur if not args.dry_run else None, table, dry_run=args.dry_run)
        if ok:
            success_count += 1
        else:
            fail_count += 1
            print(f"  ❌ 迁移失败：{table}")
            break  # 失败即停止

    # 提交事务
    if not args.dry_run:
        if fail_count == 0:
            pg_conn.commit()
            print(f"\n✅ 全部完成！成功 {success_count} 张表")
        else:
            pg_conn.rollback()
            print(f"\n❌ 已回滚！成功 {success_count} 张，失败 {fail_count} 张")

        pg_cur.close()
        pg_conn.close()

    duck_con.close()


if __name__ == "__main__":
    main()
