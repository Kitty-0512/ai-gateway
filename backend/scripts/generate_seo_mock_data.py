#!/usr/bin/env python3
"""生成 SEO 演示 CSV 到 backend/data/seo/。"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.sql.seo_data_generator import generate_all  # noqa: E402


def main() -> None:
    out_dir = ROOT / "data" / "seo"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = generate_all()
    for name, df in frames.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        print(f"Wrote {path} ({len(df)} rows)")


if __name__ == "__main__":
    main()
