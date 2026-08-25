"""One-off: force refresh SEO builtin tables after regenerating CSV."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from app.services.sql.db_utils import get_db_sync
from app.services.sql.seed_seo_data import ensure_seo_builtin_datasets


def main() -> None:
    ids = ensure_seo_builtin_datasets(force_refresh=True)
    print("dataset_ids", ids)
    db = get_db_sync()
    try:
        for table in ("ds_seo_site_traffic_daily", "ds_seo_keyword_ranking"):
            row = db.execute(
                text(f"SELECT MIN(date), MAX(date), COUNT(*) FROM `{table}`")
            ).fetchone()
            print(table, "min=", row[0], "max=", row[1], "n=", row[2])
        row = db.execute(
            text(
                """
                SELECT
                  AVG(CASE WHEN date < DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                           THEN organic_traffic END) AS early,
                  AVG(CASE WHEN date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                           THEN organic_traffic END) AS late
                FROM ds_seo_site_traffic_daily
                WHERE site = 'Site A'
                """
            )
        ).fetchone()
        print("Site A organic early/late avg:", float(row[0] or 0), float(row[1] or 0))
    finally:
        db.close()


if __name__ == "__main__":
    main()
