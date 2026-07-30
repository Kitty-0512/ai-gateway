"""
数据库连接和 SQL 安全校验。

- SQLAlchemy 连接：仅用于元数据操作（datasets 表查询、mcp_logs 写入、file_parser 建表）
- validate_sql_safe：本地正则校验，确保 SQL 是只读 SELECT
- 用户数据的实际查询已迁移到 MCP Server（见 app.core.mcp_client）
"""

import re

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_settings = get_settings()

_engine = None
_SessionLocal = None


def __getattr__(name: str):
    """模块级懒加载：兼容 `from db_utils import engine` 的旧用法。"""
    if name == "engine":
        return _get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _get_engine():
    """延迟创建数据库 engine（避免模块导入时就连接数据库）。"""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    _connect_args: dict = {
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    }

    if _settings.mysql_ssl_enabled:
        if _settings.mysql_ssl_ca:
            _ca_path = _settings.mysql_ssl_ca
        else:
            import certifi
            _ca_path = certifi.where()
        _connect_args["ssl"] = {"ca": _ca_path}

    _engine = create_engine(
        _settings.database_url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args=_connect_args,
        echo=False,
    )
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_db() -> Session:
    """FastAPI 依赖注入：获取数据库会话"""
    global _SessionLocal
    if _SessionLocal is None:
        _get_engine()
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_sync() -> Session:
    """直接获取数据库会话（非依赖注入场景）"""
    global _SessionLocal
    if _SessionLocal is None:
        _get_engine()
    return _SessionLocal()


# ---- SQL 安全校验（纯本地，不涉及数据库连接）----

SQL_BLOCK_PATTERN = re.compile(
    r"\b(DROP|ALTER|TRUNCATE|DELETE|UPDATE|INSERT|CREATE|REPLACE|GRANT|REVOKE)\s",
    re.IGNORECASE,
)
SQL_UNION_PATTERN = re.compile(
    r"(SELECT\s+.*\s+INTO\s+(OUTFILE|DUMPFILE|FILE))",
    re.IGNORECASE,
)


def validate_sql_safe(sql: str) -> None:
    """校验 SQL 安全性。只允许 SELECT 查询。"""
    stripped = sql.strip()
    if not stripped.upper().startswith("SELECT"):
        raise ValueError(f"只允许 SELECT 查询，检测到非法语句: {stripped[:100]}")
    if SQL_BLOCK_PATTERN.search(stripped):
        raise ValueError(f"SQL 包含被禁止的修改操作: {stripped[:100]}")
    if SQL_UNION_PATTERN.search(stripped):
        raise ValueError(f"SQL 包含禁止的 INTO OUTFILE 操作: {stripped[:100]}")
    cleaned = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    if SQL_BLOCK_PATTERN.search(cleaned):
        raise ValueError("SQL 注释中存在被禁止的操作")
    no_trailing = stripped.rstrip().rstrip(";")
    if ";" in no_trailing:
        cleaned_quotes = re.sub(r"'[^']*'", "", no_trailing)
        cleaned_quotes = re.sub(r'"[^"]*"', "", cleaned_quotes)
        if ";" in cleaned_quotes:
            raise ValueError(f"SQL 包含多条语句（检测到分号拼接）: {stripped[:100]}")
