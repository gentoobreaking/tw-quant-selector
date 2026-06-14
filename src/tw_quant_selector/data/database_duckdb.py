from __future__ import annotations
from typing import Optional, Any, Union
"""
T104: PostgreSQL 数据库连接层（SQLAlchemy ORM）
取代原有 DuckDB 连接层（database.py）

接口保持兼容：
- Database().connect(read_only=True|False)  → Session
- Database().connection(read_only=...)    → context manager (Session)
- Database().execute(query, params, read_only=...) → Result
- Database().init_db()                    → 创建所有表（若尚未存在）
"""
import os
import re
import threading
import structlog
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import NullPool

from .models import Base

log = structlog.get_logger()

# ============================================================
# 配置
# ============================================================
DEFAULT_DB_URL = (
    "postgresql+psycopg2://{user}:{passwd}@localhost:5432/{db}"
    .format(
        user=os.getenv("POSTGRES_USER", "tw-quant"),
        passwd=os.getenv("POSTGRES_PASSWORD", "tw-quant-PassWd"),
        db=os.getenv("POSTGRES_DB", "tw_quant"),
    )
)

ALLOWED_TABLES: frozenset[str] = frozenset({
    "stocks",
    "daily_prices",
    "monthly_revenue",
    "financials",
    "valuations",
    "signals",
    "guru_scores",
    "strategy_config_history",
    "backtest_runs",
    "backtest_positions",
    "backtest_equity",
    "alert_settings",
    "ingestion_tracker",
    "operation_logs",
    "alert_log",
    "portfolio",
    "lots",
})


def validate_table_name(name: str) -> str:
    """Validate that *name* is a known table. Returns *name* on success,
    raises ``ValueError`` otherwise."""
    if name not in ALLOWED_TABLES:
        raise ValueError("Unknown or disallowed table: " + repr(name))
    return name


# ============================================================
# ? 占位符 → :1, :2, :3 ...（兼容 DuckDB 写法）
# ============================================================
def _convert_qmark_to_named(sql: str, params: list) -> tuple[str, dict]:
    """
    把 DuckDB 风 (?, ?, ?) 转成 SQLAlchemy 风 (:1, :2, :3)
    PostgreSQL + SQLAlchemy 用命名占位符，不支持 ?
    """
    if not params or not isinstance(params, list):
        return sql, params

    counter = 0

    def replacer(_: re.Match):
        nonlocal counter
        counter += 1
        return f":{counter}"

    new_sql = re.sub(r"\?", replacer, sql)
    new_params = {str(i + 1): v for i, v in enumerate(params)}
    return new_sql, new_params


# ============================================================
# 全局 Engine / Session 工厂（singleton）
# ============================================================
_engine = None
_SessionFactory = None
_engine_lock = threading.Lock()


def get_engine(db_url: Optional[str] = None, echo: bool = False):
    """获取 / 创建 SQLAlchemy Engine（singleton）"""
    global _engine
    with _engine_lock:
        if _engine is None:
            url = db_url or DEFAULT_DB_URL
            _engine = create_engine(
                url,
                echo=echo,            # SQL 日志（调试用）
                pool_pre_ping=True,  # 自动重连
                poolclass=NullPool,   # 禁用连接池（避免多线程冲突）
            )
            # 创建所有表（若尚未存在）
            Base.metadata.create_all(_engine)
            log.info("database.engine_created", url=url.split("@")[-1])  # 不打印密码
    return _engine


def get_session_factory():
    """获取 Session 工厂（singleton）"""
    global _SessionFactory
    with _engine_lock:
        if _SessionFactory is None:
            engine = get_engine()
            _SessionFactory = scoped_session(
                sessionmaker(bind=engine, expire_on_commit=False)
            )
    return _SessionFactory


# ============================================================
# Database 类（兼容原有接口）
# ============================================================
class Database:
    """
    兼容原有 DuckDB Database 类的接口：

    原接口                    →  新实现
    ------------------------------------------------
    .connect(read_only=True)  →  Session（read-only 用 separate session）
    .connection(read_only=.)  →  context manager → Session
    .execute(query, params, read_only=.) → Result
    .init_db()                →  Base.metadata.create_all()
    .close()                  →  session_factory.remove()
    .checkpoint()             →  conn.execute("CHECKPOINT")  # PostgreSQL 不需要
    """

    def __init__(self, db_url: Optional[str] = None, read_only: bool = False):
        """
        Args:
            db_url:     PostgreSQL URL（默认从环境变量读取）
            read_only:   保留参数（兼容原有接口，PostgreSQL 用权限控制）
        """
        self.db_url = db_url or DEFAULT_DB_URL
        self.read_only = read_only
        self._engine = None
        self._session_factory = None

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(
                self.db_url,
                echo=False,
                pool_pre_ping=True,
                poolclass=NullPool,
            )
        return self._engine

    def _get_session_factory(self):
        if self._session_factory is None:
            engine = self._get_engine()
            self._session_factory = scoped_session(
                sessionmaker(bind=engine, expire_on_commit=False)
            )
        return self._session_factory

    def connect(self, read_only: Optional[bool] = None) -> "Session":
        """
        返回 SQLAlchemy Session（兼容原有 conn 对象）

        原有代码用 duckdb 的 conn.execute()，现在改成 Session.execute()
        """
        factory = self._get_session_factory()
        session = factory()
        return session

    def close(self):
        """关闭 session（兼容原有接口）"""
        try:
            factory = self._get_session_factory()
            factory.remove()
        except Exception as e:
            log.warning("database.close_failed", error=str(e))

    def checkpoint(self):
        """PostgreSQL 不需要 CHECKPOINT（WAL 自动管理）"""
        pass  # 保留接口兼容性

    @contextmanager
    def connection(self, read_only: Optional[bool] = None):
        """
        Context manager：返回 Session，自动 commit/rollback

        用法：
            with db.connection(read_only=True) as session:
                result = session.execute(text("SELECT 1"))
        """
        factory = self._get_session_factory()
        session = factory()
        try:
            yield session
            if not (read_only if read_only is not None else self.read_only):
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            factory.remove()

    def execute(self, query: str, params: list | Optional[dict] = None, read_only: Optional[bool] = None):
        """
        执行原生 SQL（兼容原有 duckdb conn.execute() 接口）

        ✅ 自动把 DuckDB 风 ? 占位符转成 SQLAlchemy 风 :1, :2, :3
          上层 app.py 完全不用改

        ✅ 修复 cursor closed 问题：session 不立刻关闭，
          由调用方通过 db.close() 或 context manager 关闭
        """
        is_select = query.strip().upper().startswith("SELECT")
        read_only = read_only if read_only is not None else (self.read_only or is_select)

        # 兼容 DuckDB 风 ? 占位符 → 转成 :1, :2, :3 ...
        if isinstance(params, list):
            query, params = _convert_qmark_to_named(query, params)

        # 直接创建 session，不包 with（避免提前关闭）
        factory = self._get_session_factory()
        session = factory()
        result = session.execute(text(query), params)
        
        # 关键：SELECT 先 fetchall()，非 SELECT 返回 result
        if is_select:
            rows = result.fetchall()
            # 关闭 session（fetchall 后数据已拿到）
            factory.remove()
            return rows  # list[tuple]，兼容原有代码
        else:
            # 非 SELECT：不关 session，让调用方自己处理（或返回 rowcount）
            # 为了兼容，这里 commit 后关 session
            if not read_only:
                session.commit()
            factory.remove()
            return result

    def init_db(self):
        """
        初始化数据库（创建所有表）
        PostgreSQL 用 Base.metadata.create_all()，不会重复创建已存在的表
        """
        engine = self._get_engine()
        Base.metadata.create_all(engine)
        log.info("database.init_db_success")

    def change_path(self, new_url: str):
        """
        切换数据库（兼容原有 change_path 接口）

        Args:
            new_url: PostgreSQL URL 或文件路径（兼容 DuckDB 路径）
        """
        # 如果是 DuckDB 路径，忽略（不再支持）
        if new_url.endswith(".duckdb"):
            log.warning("database.duckdb_path_ignored", path=new_url)
            return self.db_url

        self.close()
        self.db_url = new_url
        self._engine = None
        self._session_factory = None
        self.init_db()
        return self.db_url


# ============================================================
# 便捷函数（全局使用）
# ============================================================
def get_db() -> Database:
    """获取全局 Database 实例"""
    return Database()


def get_session() -> "Session":
    """获取新 Session（用完需 close）"""
    return get_db().connect()


@contextmanager
def db_session(read_only: bool = False):
    """
    便捷 context manager：

    with db_session(read_only=True) as session:
        session.execute(text("SELECT 1"))
    """
    db = get_db()
    with db.connection(read_only=read_only) as session:
        yield session
