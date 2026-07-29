"""
文件解析服务。

支持 .xlsx / .xls / .csv 文件上传，自动推断字段类型，
动态建表并写入 MySQL。解析结果同步写入 datasets 表。
"""

import os
import re
import time
import json
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
import pandas as pd
from sqlalchemy import text, inspect
from sqlalchemy.types import VARCHAR, DECIMAL, DATE, Float, String
from app.services.sql.db_utils import engine, get_db_sync
from app.models.sql_schemas import FileUploadResponse


# 合法的 MySQL 表名：仅字母、数字、下划线，且不以数字开头
TABLE_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def sanitize_table_name(original: str) -> str:
    """
    将文件名转换为合法的 MySQL 表名。

    规则：
    - 去扩展名
    - 特殊字符替换为下划线
    - 加时间戳后缀防重名
    - 前缀 'ds_' 标识这是数据集表
    """
    base = os.path.splitext(original)[0]
    base = re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fa5]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "dataset"
    if base[0].isdigit():
        base = "d_" + base
    ts = int(time.time())
    return f"ds_{base}_{ts}"


def infer_sql_type(dtype: str, series: pd.Series) -> str:
    """
    根据 pandas dtype 和样本数据推断 MySQL 字段类型。

    规则：
    - 整型 → BIGINT
    - 浮点 → DECIMAL(18,4)
    - 日期 → DATE
    - 日期时间 → DATETIME
    - 其他 → VARCHAR(500)
    """
    dtype_str = str(dtype).lower()

    if "int" in dtype_str:
        return "BIGINT"
    elif "float" in dtype_str or "double" in dtype_str:
        return "DECIMAL(18,4)"
    elif "datetime" in dtype_str or "timestamp" in dtype_str:
        return "DATETIME"
    elif "date" in dtype_str:
        return "DATE"
    else:
        # 检查是否能转为数字(有小数点的)
        sample = series.dropna().head(10)
        if len(sample) > 0:
            try:
                sample.astype(float)
                return "DECIMAL(18,4)"
            except (ValueError, TypeError):
                pass
            try:
                # 要求至少包含 YYYY-MM-DD 格式，避免 "2024-01" 被误认
                sample_str = sample.astype(str)

                # 如果所有非空值都匹配 YYYY-MM-DD 或 YYYY/MM/DD 才判断为日期
                date_pattern = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")
                if not all(
                    pd.isna(v) or bool(date_pattern.match(str(v)))
                    for v in sample_str
                ):
                    raise ValueError("not a standard date")

                pd.to_datetime(sample, errors="raise")
                return "DATE"
            except (ValueError, TypeError):
                pass
        return "VARCHAR(500)"


def clean_value(val: Any) -> Any:
    """清洗单个值：NaN/NaT/None 转为 None，numpy 类型转原生"""
    if val is None:
        return None
    if isinstance(val, float) and pd.isna(val):
        return None
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    if hasattr(val, "item"):
        return val.item()
    return val


def parse_file(file_path: str, original_filename: str) -> FileUploadResponse:
    """
    解析文件并写入数据库。

    Args:
        file_path: 上传文件的本地路径（用于格式判断，实际读取由调用方决定）
        original_filename: 原始文件名

    Returns:
        FileUploadResponse: 包含表名、行数、字段列表
    """
    return parse_file_content(Path(file_path).read_bytes(), original_filename)


def parse_file_content(content: bytes, original_filename: str) -> FileUploadResponse:
    """
    从字节内容解析文件并写入数据库。

    与 parse_file 相同，但直接从内存读取，不依赖文件系统。
    这是通过 MCP 读取文件后的入口。
    """

    ext = os.path.splitext(original_filename)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(BytesIO(content), encoding="utf-8-sig")
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(BytesIO(content), engine="openpyxl")
    else:
        raise ValueError(f"不支持的文件格式: {ext}，仅支持 .csv / .xlsx / .xls")

    if df.empty:
        raise ValueError("文件内容为空，无法解析")

    # 清洗列名
    df.columns = [
        re.sub(r"[^a-zA-Z0-9_\u4e00-\u9fa5]", "_", str(c)).strip("_") or f"col_{i}"
        for i, c in enumerate(df.columns)
    ]

    # 推断字段类型并构建建表 DDL
    type_mapping = {}
    schema_list = []
    for col in df.columns:
        col_type = infer_sql_type(str(df[col].dtype), df[col])
        type_mapping[col] = col_type
        schema_list.append({"name": col, "type": col_type, "comment": col})

    # 生成表名
    table_name = sanitize_table_name(original_filename)

    # 清洗所有值
    for col in df.columns:
        df[col] = df[col].apply(clean_value)

    # 建表并写入
    db = get_db_sync()
    try:
        ddl_columns = []
        for col, col_type in type_mapping.items():
            safe_col = f"`{col}`"
            ddl_columns.append(f"{safe_col} {col_type}")

        ddl = (
            f"CREATE TABLE IF NOT EXISTS `{table_name}` (\n"
            f"    `_id` BIGINT AUTO_INCREMENT PRIMARY KEY,\n"
            f"    {', '.join(ddl_columns)}\n"
            f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )

        db.execute(text(ddl))
        db.commit()

        # 批量插入
        cols_placeholder = ", ".join([f"`{c}`" for c in df.columns])
        vals_placeholder = ", ".join([f":{c}" for c in df.columns])
        insert_sql = text(
            f"INSERT INTO `{table_name}` ({cols_placeholder}) VALUES ({vals_placeholder})"
        )

        batch = []
        for _, row in df.iterrows():
            batch.append({c: clean_value(row[c]) for c in df.columns})

        for i in range(0, len(batch), 500):
            chunk = batch[i:i + 500]
            db.execute(insert_sql, chunk)
        db.commit()

        # 查询实际写入行数
        count_result = db.execute(text(f"SELECT COUNT(*) AS cnt FROM `{table_name}`"))
        row_count = count_result.fetchone()[0]

        return FileUploadResponse(
            table_name=table_name,
            row_count=row_count,
            columns=schema_list,
        )

    except Exception as e:
        db.rollback()
        # 清理建表
        try:
            db.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
            db.commit()
        except Exception:
            pass
        raise ValueError(f"数据写入失败: {str(e)}")
    finally:
        db.close()
