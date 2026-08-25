"""
内置 SEO 演示数据集 — 启动时自动灌库。

固定表名：
- ds_seo_site_traffic_daily
- ds_seo_keyword_ranking

datasets 表用 file_name = '__builtin__:...' 标记，重复启动时跳过重建。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import text

from app.services.sql.db_utils import get_db_sync
from app.services.sql.seo_data_generator import generate_all

logger = logging.getLogger(__name__)

BUILTIN_DATASETS: dict[str, dict[str, Any]] = {
    "seo_site_traffic_daily": {
        "file_name": "__builtin__:seo_site_traffic_daily",
        "table_name": "ds_seo_site_traffic_daily",
        "file_type": "csv",
        "display_name": "SEO 站点流量（内置）",
        "schema": [
            {"name": "site", "type": "VARCHAR(50)", "comment": "站点名称"},
            {"name": "date", "type": "DATE", "comment": "日期"},
            {"name": "pv", "type": "BIGINT", "comment": "页面浏览量"},
            {"name": "uv", "type": "BIGINT", "comment": "独立访客数"},
            {"name": "organic_traffic", "type": "BIGINT", "comment": "自然搜索流量"},
        ],
        "dataframe_key": "site_traffic_daily",
    },
    "seo_keyword_ranking": {
        "file_name": "__builtin__:seo_keyword_ranking",
        "table_name": "ds_seo_keyword_ranking",
        "file_type": "csv",
        "display_name": "SEO 关键词排名（内置）",
        "schema": [
            {"name": "site", "type": "VARCHAR(50)", "comment": "站点名称"},
            {"name": "keyword", "type": "VARCHAR(200)", "comment": "关键词"},
            {"name": "rank", "type": "BIGINT", "comment": "搜索排名（越小越好）"},
            {"name": "date", "type": "DATE", "comment": "日期"},
        ],
        "dataframe_key": "keyword_ranking",
    },
}

_DATA_CSV_DIR = Path(__file__).resolve().parents[3] / "data" / "seo"


def _load_dataframe(key: str) -> pd.DataFrame:
    """优先读 CSV；缺失时内存生成。"""
    csv_path = _DATA_CSV_DIR / f"{key}.csv"
    if csv_path.is_file():
        return pd.read_csv(csv_path, encoding="utf-8-sig")
    frames = generate_all()
    return frames[key]


def _create_and_fill_table(db, table_name: str, schema: list[dict], df: pd.DataFrame) -> int:
    db.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
    col_defs = ", ".join(f"`{c['name']}` {c['type']}" for c in schema)
    ddl = (
        f"CREATE TABLE `{table_name}` ("
        f"`_id` BIGINT AUTO_INCREMENT PRIMARY KEY, {col_defs}"
        f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    )
    db.execute(text(ddl))

    cols = [c["name"] for c in schema]
    placeholders = ", ".join(f"`{c}`" for c in cols)
    vals = ", ".join(f":{c}" for c in cols)
    insert_sql = text(f"INSERT INTO `{table_name}` ({placeholders}) VALUES ({vals})")

    batch = [{c: (None if pd.isna(row[c]) else row[c]) for c in cols} for _, row in df.iterrows()]
    for i in range(0, len(batch), 500):
        db.execute(insert_sql, batch[i : i + 500])

    cnt = db.execute(text(f"SELECT COUNT(*) FROM `{table_name}`")).scalar()
    return int(cnt or 0)


def _find_existing_ids(db) -> list[int]:
    markers = [m["file_name"] for m in BUILTIN_DATASETS.values()]
    placeholders = ", ".join(f":m{i}" for i in range(len(markers)))
    params = {f"m{i}": m for i, m in enumerate(markers)}
    rows = db.execute(
        text(
            f"SELECT id FROM datasets WHERE file_name IN ({placeholders}) "
            f"AND status = 1 ORDER BY id"
        ),
        params,
    ).fetchall()
    return [int(r[0]) for r in rows]


def ensure_seo_builtin_datasets() -> list[int]:
    """
    确保内置 SEO 数据集已灌入 MySQL 并登记在 datasets 表。

    Returns:
        两个内置数据集的 dataset id 列表（traffic, keyword）
    """
    db = get_db_sync()
    try:
        existing = _find_existing_ids(db)
        if len(existing) >= len(BUILTIN_DATASETS):
            logger.info("内置 SEO 数据集已存在，dataset_ids=%s", existing)
            return existing[: len(BUILTIN_DATASETS)]

        frames = generate_all()
        ids: list[int] = []

        for spec in BUILTIN_DATASETS.values():
            # 单表幂等：若已登记则跳过
            row = db.execute(
                text("SELECT id FROM datasets WHERE file_name = :fn AND status = 1 LIMIT 1"),
                {"fn": spec["file_name"]},
            ).fetchone()
            if row:
                ids.append(int(row[0]))
                continue

            df_key = spec["dataframe_key"]
            df = _load_dataframe(df_key)
            row_count = _create_and_fill_table(db, spec["table_name"], spec["schema"], df)

            db.execute(
                text("""
                    INSERT INTO datasets
                        (file_name, file_path, file_size, table_name,
                         schema_json, row_count, file_type, uploaded_by, status)
                    VALUES
                        (:file_name, :file_path, :file_size, :table_name,
                         :schema_json, :row_count, :file_type, 1, 1)
                """),
                {
                    "file_name": spec["file_name"],
                    "file_path": str(_DATA_CSV_DIR / f"{df_key}.csv"),
                    "file_size": 0,
                    "table_name": spec["table_name"],
                    "schema_json": json.dumps(spec["schema"], ensure_ascii=False),
                    "row_count": row_count,
                    "file_type": spec["file_type"],
                },
            )
            db.commit()
            new_id = db.execute(text("SELECT LAST_INSERT_ID()")).scalar()
            ids.append(int(new_id))
            logger.info(
                "已灌入内置数据集 %s → table=%s rows=%d id=%s",
                spec["display_name"],
                spec["table_name"],
                row_count,
                new_id,
            )

        return ids
    except Exception as exc:
        db.rollback()
        logger.warning("内置 SEO 数据集灌库失败（可忽略，无 DB 时正常）: %s", exc)
        return []
    finally:
        db.close()


def get_default_dataset_ids() -> list[int]:
    """查询已登记的内置 SEO dataset id（不触发灌库）。"""
    db = get_db_sync()
    try:
        return _find_existing_ids(db)
    except Exception:
        return []
    finally:
        db.close()
