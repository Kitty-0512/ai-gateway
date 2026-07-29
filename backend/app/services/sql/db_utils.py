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

engine = create_engine(
    _settings.database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    },
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """FastAPI 依赖注入：获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_sync() -> Session:
    """直接获取数据库会话（非依赖注入场景）"""
    return SessionLocal()


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
