"""
SEO 经营分析演示数据集生成器。

产出两张表：
- site_traffic_daily: site, date, pv, uv, organic_traffic
- keyword_ranking: site, keyword, rank, date

设计意图（供 Demo / Evaluation 使用）：
- Site A：近 30 天流量下降约 23%（自然搜索同步下滑）
- Site B：近 30 天流量增长约 15%
- Site C：近 30 天流量微降约 5%
- Site A：15 个核心关键词在近 30 天排名下降超过 3 位
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

SITES = ("Site A", "Site B", "Site C")
START_DATE = date(2024, 6, 1)
DAYS = 60

# 近 30 天相对前 30 天的 PV 变化率（用于构造「下降最多」类问题）
SITE_PV_TREND = {
    "Site A": (10_000, 7_700),   # -23%
    "Site B": (8_000, 9_200),    # +15%
    "Site C": (6_000, 5_700),    # -5%
}

KEYWORD_POOL = [
    "笔记本电脑", "机械键盘", "无线鼠标", "显示器", "办公椅",
    "打印机", "路由器", "固态硬盘", "内存条", "显卡",
    "主板", "电源", "机箱", "摄像头", "麦克风",
    "耳机", "平板", "手机壳", "数据线", "充电器",
    "投影仪", "扫描仪", "碎纸机", "订书机", "文件柜",
    "白板", "会议系统", "网络交换机", "防火墙", "服务器",
    "云存储", "备份软件", "杀毒软件", "CRM系统", "ERP系统",
    "数据分析", "SEO优化", "内容营销", "社交媒体", "电子邮件营销",
    "转化率优化", "用户体验", "页面速度", "移动端适配", "结构化数据",
    "外链建设", "关键词研究", "竞争对手分析", "网站审计", "流量分析",
]

# Site A 上会在后半段排名下滑的核心词（前 15 个）
SITE_A_DECLINING_KEYWORDS = KEYWORD_POOL[:15]


def _date_range() -> list[date]:
    return [START_DATE + timedelta(days=i) for i in range(DAYS)]


def _lerp(start: float, end: float, t: float) -> int:
    return max(1, int(round(start + (end - start) * t)))


def generate_site_traffic_daily() -> pd.DataFrame:
    rows: list[dict] = []
    dates = _date_range()
    mid = len(dates) // 2

    for site in SITES:
        pv_start, pv_end = SITE_PV_TREND[site]
        for i, d in enumerate(dates):
            t = 0.0 if i < mid else (i - mid) / max(mid - 1, 1)
            pv = _lerp(pv_start, pv_end, t) if i >= mid else pv_start
            # UV ≈ 65% of PV；organic ≈ 55% of PV（Site A 后期 organic 跌得更狠）
            uv = max(1, int(pv * 0.65))
            organic_ratio = 0.55 if site != "Site A" or i < mid else 0.42
            organic = max(1, int(pv * organic_ratio))
            rows.append({
                "site": site,
                "date": d.isoformat(),
                "pv": pv,
                "uv": uv,
                "organic_traffic": organic,
            })

    return pd.DataFrame(rows)


def generate_keyword_ranking() -> pd.DataFrame:
    rows: list[dict] = []
    dates = _date_range()
    mid = len(dates) // 2

    for site in SITES:
        keywords = KEYWORD_POOL[:50]
        for kw_idx, keyword in enumerate(keywords):
            base_rank = 5 + (kw_idx % 20)
            for i, d in enumerate(dates):
                rank = base_rank
                if site == "Site A" and keyword in SITE_A_DECLINING_KEYWORDS and i >= mid:
                    # 后半段排名下降 4~8 位
                    drop = 4 + (kw_idx % 5)
                    rank = base_rank + drop
                elif site == "Site B" and i >= mid:
                    rank = max(1, base_rank - 1)
                rows.append({
                    "site": site,
                    "keyword": keyword,
                    "rank": rank,
                    "date": d.isoformat(),
                })

    return pd.DataFrame(rows)


def generate_all() -> dict[str, pd.DataFrame]:
    return {
        "site_traffic_daily": generate_site_traffic_daily(),
        "keyword_ranking": generate_keyword_ranking(),
    }
