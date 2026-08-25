"""Re-run phase-1 smoke questions against /api/chat/stream."""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

QUESTIONS = [
    "最近7天PV是多少？",
    "最近30天SEO流量趋势怎么样？",
    "本周和上周自然流量相比有什么变化？",
    "最近30天SEO流量为什么下降？",
    "哪些关键词导致了下降？",
]

OUT = Path(__file__).resolve().parents[2] / "docs" / "_smoke_raw_dated.json"


def run_one(q: str) -> list[tuple[str, dict]]:
    body = json.dumps(
        {
            "question": q,
            "content": q,
            "mode": "sql",
            "dataset_ids": [7, 8],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/chat/stream",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    events: list[tuple[str, dict]] = []
    with urllib.request.urlopen(req, timeout=420) as resp:
        event = None
        data_lines: list[str] = []
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "":
                if event is not None:
                    payload = "\n".join(data_lines)
                    try:
                        data = json.loads(payload) if payload else {}
                    except Exception:
                        data = {"_raw": payload[:400]}
                    events.append((event, data))
                event = None
                data_lines = []
    return events


def summarize(q: str, evs: list[tuple[str, dict]]) -> dict:
    routing = next((d for e, d in evs if e == "routing"), {})
    plan = next((d for e, d in evs if e == "plan"), {})
    tool_dones = [d for e, d in evs if e == "tool_done"]
    stages = [d.get("stage") for e, d in evs if e == "stage"]
    result = next((d for e, d in evs if e == "result"), {})
    sqls = [d.get("sql") for e, d in evs if e == "sql" and d.get("sql")]
    has_synth = any(s == "synthesizing" for s in stages) or bool(
        result.get("synthesized")
    )
    return {
        "q": q,
        "router": routing.get("tool"),
        "router_reason": routing.get("reason"),
        "plan_source": plan.get("source"),
        "plan_steps": plan.get("steps"),
        "needs_synthesis": plan.get("needs_synthesis"),
        "tool_dones": [
            {
                "step": t.get("step"),
                "tool": t.get("tool"),
                "status": t.get("status"),
            }
            for t in tool_dones
        ],
        "exec_steps": len(tool_dones),
        "synthesizer": has_synth,
        "sql_count": len(sqls),
        "sql_preview": [(s or "")[:160] for s in sqls],
        "top_sql": (result.get("sql") or "")[:160],
        "result_row_count": len(result.get("result") or []),
        "has_top_sql": bool(result.get("sql")),
        "has_top_chart": bool(result.get("chart_config")),
        "answer_preview": (result.get("answer") or "")[:220],
        "error": result.get("error"),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    summary = []
    for i, q in enumerate(QUESTIONS, 1):
        print(f"=== Q{i}: {q} ===", flush=True)
        try:
            evs = run_one(q)
            row = summarize(q, evs)
        except Exception as exc:  # noqa: BLE001
            row = {"q": q, "error": repr(exc)}
        print(json.dumps(row, ensure_ascii=False, indent=2), flush=True)
        summary.append(row)
    OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
