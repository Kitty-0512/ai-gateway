"""诊断 Prompt：要求输出严格 JSON，并引用日志原文证据。"""

from app.models.log_schemas import LogType

SYSTEM_PROMPT = """你是资深 SRE / 运维工程师，擅长从日志中做根因分析。
你只处理三类日志：Docker、Nginx、应用异常栈（app_stack）。
必须基于日志原文推理，禁止编造日志中不存在的服务名、错误码或堆栈信息。
证据（evidence）必须是日志中的原文片段（可截短），不要改写。

关于追问（follow_up_questions）：
- 首次诊断时，若缺少发版情况、是否偶发、影响服务等关键上下文，必须提出 1～3 个短问题。
- 典型问题示例：是否刚发版、是偶发还是持续、影响哪个服务/路径。
- 若用户已提供补充上下文（二次诊断），应融合日志与补充信息给出更准确结论；
  信息充分时 follow_up_questions 必须为空数组；仍不足时可再问 1～2 个，但不要重复已回答过的问题。

只输出一个 JSON 对象，不要 Markdown 代码块，不要额外解释。"""


JSON_SCHEMA_HINT = """
输出 JSON 字段（全部必填）：
{
  "log_type": "docker" | "nginx" | "app_stack" | "unknown",
  "anomaly_type": "简短异常类型",
  "root_cause": "可能根因（基于证据与补充信息）",
  "investigation_steps": ["排查步骤1", "步骤2", "..."],
  "risk_level": "low" | "medium" | "high" | "critical",
  "evidence": ["日志原文片段1", "片段2"],
  "summary": "一句话结论",
  "follow_up_questions": ["可选反问1", "..."]
}
""".strip()


def build_user_prompt(
    cleaned_log: str,
    detected_type: LogType,
    candidate_evidence: list[str],
    extra_context: str | None = None,
    search_context: str | None = None,
) -> str:
    evidence_block = "\n".join(f"- {e}" for e in candidate_evidence) or "- （无）"
    hinted = detected_type.value
    has_context = bool(extra_context and extra_context.strip())
    context_block = extra_context.strip() if has_context else "（无）"
    has_search = bool(search_context and search_context.strip())

    if has_context:
        task = f"""这是【追问回答】：用户基于同一份日志继续提问。
用户问题：{context_block}

请直接回答用户问题，不要重复输出完整诊断报告。
要求：
- summary 字段写「给用户的直接回答」（2-6 句话，聚焦问题本身）
- 可结合日志证据，但不要重新罗列现象/根因/排查步骤等完整模板
- investigation_steps 如无必要返回空数组 []
- follow_up_questions 如无必要返回空数组 []
- root_cause / anomaly_type 等字段可简要填写，前端不会完整展示"""
    elif has_search:
        task = """这是【首次诊断 + 外部知识增强】。
系统已自动搜索了与该错误相关的网络资料（见下方）。
请结合日志原文和外部参考资料，给出更准确的根因与排查建议。
外部资料可能包含类似错误的解决方案、官方文档说明或社区讨论，请加以甄别后引用。"""
    else:
        task = """这是【首次诊断】。
请给出初步结论；同时提出 1～3 个对定位最有帮助的澄清问题（follow_up_questions），
优先围绕：是否刚发版、是否偶发/持续、影响哪个服务或路径。"""

    search_block = ""
    if has_search:
        search_block = f"""
----- 外部检索结果（仅供参考，需甄别） -----
{search_context}
----- 外部检索结束 -----

"""

    return f"""请分析以下运维日志并给出结构化诊断。

{task}

预判日志类型（可纠正）：{hinted}

候选证据行（优先从中选用，也可直接引用日志其他原文）：
{evidence_block}

用户补充上下文：
{context_block}
{search_block}
----- 日志原文（已清洗/可能截断） -----
{cleaned_log}
----- 日志结束 -----

{JSON_SCHEMA_HINT}
"""
