from __future__ import annotations
"""
T104: PostgreSQL 数据库连接层（SQLAlchemy ORM）
取代原有 DuckDB 连接层（database.py）

接口保持兼容：
- Database().connect(read_only=True|False)  → Session
- Database().connection(read_only=...)    → context manager (Session)
- Database().execute(query, params, read_only=...) → ResultSet（有 .fetchall() / .fetchone()）
- Database().init_db()                    → 创建所有表（若尚未存在）
"""
import os
import re
import threading
import structlog
from typing import Optional, Any
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session, Session
from sqlalchemy.pool import QueuePool

from .models import Base

log = structlog.get_logger()

# ============================================================
# 配置
# ============================================================
DEFAULT_DB_URL = (
    "postgresql+psycopg2://{user}:{passwd}@{host}:5432/{db}"
    .format(
        user=os.getenv("POSTGRES_USER", "tw-quant"),
        passwd=os.getenv("POSTGRES_PASSWORD", "tw-quant-PassWd"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
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
    "alert_history",
    "alert_cooldowns",
    "realtime_prices",
    "portfolio",
    "lots",
    "institutional_flows",
    "institutional_holdings",
    "realtime_quotes",
    "intraday_snapshots",
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
# ResultSet — 兼容 DuckDB conn.execute() 返回对象
# ============================================================
class ResultSet:
    """
    兼容 DuckDB pyrelation 的接口：

    - .fetchall()  → list[tuple]
    - .fetchone()  → tuple 
    - 可迭代       → for row in result:

    session 在 __init__ 里已经 fetchall() 拿完数据，所以 cursor 不会早关。
    """
    def __init__(self, rows: list):
        self._rows = rows
        self._idx = 0

    def fetchall(self):
        """返回所有行（list[tuple]）"""
        return self._rows

    def fetchone(self):
        """返回下一行（tuple），没有则返回 None"""
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def __iter__(self):
        return iter(self._rows)

    def __len__(self):
        return len(self._rows)


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
                echo=echo,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=5,
                pool_recycle=3600,
            )
            # 创建所有表（若尚未存在）
            Base.metadata.create_all(_engine)
            log.info("database.engine_created", url=url.split("@")[-1])
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
    -----------------------------------------------
    .connect(read_only=True)  →  Session（read-only 用 separate session）
    .connection(read_only=.)  →  context manager → Session
    .execute(query, params, read_only=.) → ResultSet（有 .fetchall()）
    .init_db()                →  Base.metadata.create_all()
    .close()                  →  session_factory.remove()
    .checkpoint()             →  conn.execute("CHECKPOINT")  # PostgreSQL 不需要
    """

    def __init__(self, db_url: str  = None, read_only: bool = False):
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
                pool_size=5,
                max_overflow=5,
                pool_recycle=3600,
                pool_timeout=30,
                connect_args={"connect_timeout": 10},
            )
        return self._engine

    def _get_session_factory(self):
        if self._session_factory is None:
            engine = self._get_engine()
            self._session_factory = scoped_session(
                sessionmaker(bind=engine, expire_on_commit=False)
            )
        return self._session_factory

    def connect(self, read_only: bool  = None) -> "Session":
        """
        返回 SQLAlchemy Session（兼容原有 conn 对象）

        原有代码用 duckdb 的 conn.execute()，现在改成 Session.execute()
        """
        factory = self._get_session_factory()
        session = factory()

        # Patch session.execute + result：原始 SQL 字符串自动 wrap，Result 兼容 fetchone/fetchall
        def _patch_result(result):
            _cached = None
            def _all():
                nonlocal _cached
                if _cached is None:
                    _cached = result.all()
                return _cached
            result.fetchall = _all
            result.fetchone = lambda: result.first()
            return result

        def _auto_execute(statement, params=None, **kwargs):
            if isinstance(statement, str):
                if isinstance(params, list):
                    sql_str, params = _convert_qmark_to_named(statement, params)
                    statement = text(sql_str)
                elif params and isinstance(params, dict) and "?" in statement:
                    counter = [0]
                    def _replacer(_):
                        counter[0] += 1
                        return f":p{counter[0]}"
                    new_sql = re.sub(r"\?", _replacer, statement)
                    new_params = {"p"+str(i+1): v for i, v in enumerate(params.values())}
                    statement = text(new_sql)
                    params = new_params
                elif "?" in statement:
                    counter = [0]
                    def _replacer(_):
                        counter[0] += 1
                        return f":p{counter[0]}"
                    new_sql = re.sub(r"\?", _replacer, statement)
                    statement = text(new_sql)
                    params = {}
                else:
                    statement = text(statement)
            return _patch_result(session._execute_internal(statement, params, **kwargs))

        session.execute = _auto_execute
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
    def connection(self, read_only: bool  = None):
        """
        Context manager：返回 Session，自动 commit/rollback

        用法：
            with db.connection(read_only=True) as session:
                result = session.execute(text("SELECT 1"))

        注意：返回的 session.execute() 自动兼容原始 SQL 字符串和 ?
          占位符（无需手动 wrap text()）
        """
        factory = self._get_session_factory()
        session = factory()

        # Monkey-patch session.execute：原始字符串 SQL 自动 wrap text()
        _orig_execute = session.execute
        def _auto_execute(statement, params=None, **kwargs):
            if isinstance(statement, str):
                # 自动把 DuckDB 风 ? 转成命名参数（list 时）或直接 wrap
                if isinstance(params, list):
                    sql_str, params = _convert_qmark_to_named(statement, params)
                    statement = text(sql_str)
                elif params and isinstance(params, dict) and "?" in statement:
                    counter = [0]
                    def _replacer(_):
                        counter[0] += 1
                        return f":p{counter[0]}"
                    new_sql = re.sub(r"\?", _replacer, statement)
                    new_params = {"p"+str(i+1): v for i, v in enumerate(params.values())}
                    statement = text(new_sql)
                    params = new_params
                elif "?" in statement:
                    counter = [0]
                    def _replacer(_):
                        counter[0] += 1
                        return f":p{counter[0]}"
                    new_sql = re.sub(r"\?", _replacer, statement)
                    statement = text(new_sql)
                    params = {}
                else:
                    statement = text(statement)
            return _orig_execute(statement, params, **kwargs)

        session.execute = _auto_execute

        def _patch_result(result):
            """Result → 兼容 fetchone/fetchall（对齐 DuckDB / psycopg2 行为）"""
            # 内部缓存 all() 结果
            _cached = None
            def _all():
                nonlocal _cached
                if _cached is None:
                    _cached = result.all()
                return _cached
            result.fetchall = _all
            result.fetchone = lambda: result.first()
            return result

        # Patch session.execute to return patched result
        def _auto_execute_patched(statement, params=None, **kwargs):
            r = _auto_execute(statement, params, **kwargs)
            return _patch_result(r)

        session.execute = _auto_execute_patched

        try:
            yield session
            if not (read_only if read_only is not None else self.read_only):
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            factory.remove()

    def execute(self, query: str, params: Optional[Any] = None, read_only: Optional[bool] = None):
        """
        执行原生 SQL（兼容原有 duckdb conn.execute() 接口）

        ✅ 自动把 DuckDB 风 ? 占位符转成 SQLAlchemy 风 :1, :2, :3
          上层 app.py 完全不用改

        ✅ 返回 ResultSet（有 .fetchall() / .fetchone()），对齐 DuckDB 行为
           session 在 ResultSet.__init__ 里已经关闭（数据已 fetchall 拿完）

        Returns:
            ResultSet（SELECT）
            Result（非 SELECT，如 INSERT/UPDATE/DELETE）
        """
        # 支持传入 text() 对象（SQLAlchemy 2.0 兼容）
        if hasattr(query, '_is_text_clause') and query._is_text_clause:
            sql_obj = query
            sql_str = str(query)
        else:
            sql_obj = text(query)
            sql_str = query

        is_select = sql_str.strip().upper().startswith("SELECT")
        # Resolve read_only:
        #  - explicit read_only arg wins
        #  - else: respect self.read_only (write-protected DB)
        #  - else: SELECT is naturally read-only
        read_only = read_only if read_only is not None else (self.read_only or is_select)
        # When the DB is write-protected AND the caller didn't override,
        # the commit will be silently skipped. Surface a warning so this
        # is no longer a silent no-op.
        if self.read_only and not is_select and read_only:
            import structlog
            structlog.get_logger().warning(
                "database.execute.write_skipped_read_only",
                sql_preview=sql_str.strip()[:80],
            )

        # 兼容 DuckDB 风 ? 占位符 → 转成 SQLAlchemy 命名参数
        if isinstance(params, list):
            sql_str, params = _convert_qmark_to_named(sql_str, params)
            sql_obj = text(sql_str)
        elif isinstance(params, dict) and "?" in sql_str:
            # dict params + ? placeholders: replace ? with :p1,:p2... in order
            counter = [0]
            def _replacer(_):
                counter[0] += 1
                return f":p{counter[0]}"
            new_sql = re.sub(r"\?", _replacer, sql_str)
            new_params = {"p"+str(i+1): v for i, v in enumerate(params.values())}
            sql_str = new_sql
            sql_obj = text(sql_str)
            params = new_params
        elif "?" in sql_str:
            # params=None / empty dict / other + ? placeholders: convert anyway
            counter = [0]
            def _replacer(_):
                counter[0] += 1
                return f":p{counter[0]}"
            new_sql = re.sub(r"\?", _replacer, sql_str)
            sql_str = new_sql
            sql_obj = text(sql_str)
            params = {}

        # if no conversion happened and query is a string, wrap in text()
        if hasattr(sql_obj, '_is_text_clause') and sql_obj._is_text_clause:
            pass  # already a TextClause (e.g. from _convert_qmark_to_named returned string)
        elif not (hasattr(query, '_is_text_clause') and query._is_text_clause):
            sql_obj = text(sql_str)

        factory = self._get_session_factory()
        session = factory()
        try:
            result = session.execute(sql_obj, params)

            if is_select:
                # SELECT：先 fetchall 拿数据，再关 session（避免 cursor closed）
                rows = result.fetchall()
                factory.remove()
                return ResultSet(rows)
            else:
                # 非 SELECT：commit 后关 session，返回 result（或 rowcount）
                if not read_only:
                    session.commit()
                factory.remove()
                return result
        except Exception:
            session.rollback()
            raise

    def init_db(self):
        """
        初始化数据库（创建所有表）
        PostgreSQL 用 Base.metadata.create_all()，不会重复创建已存在的表
        """
        engine = self._get_engine()
        Base.metadata.create_all(engine)
        from sqlalchemy import text as sql_text
        with engine.connect() as conn:
            conn.execute(sql_text("ALTER TABLE backtest_runs ADD COLUMN IF NOT EXISTS benchmark VARCHAR(10) DEFAULT '0050'"))
            conn.commit()
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
