"""
Text-to-SQL 对话链服务。

核心流程：
1. 查询表 Schema + 样本数据
2. 构建 Prompt 调用 LLM 生成 SQL
3. 通过 MCP Tool 执行 SQL
4. 生成文字分析 + 推荐图表
5. 持久化消息并返回完整响应
"""

import json
import re
import logging
from datetime import datetime
from typing import Any
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from sqlalchemy import text

from app.models.sql_schemas import (
    QueryRequest, QueryResponse, ChartConfig, ChartType, Message,
)
from app.services.sql.db_schema import list_user_tables
from app.services.sql.prompt_templates import (
    TEXT_TO_SQL_PROMPT, SIMPLE_ANALYSIS_PROMPT, STRUCTURED_ANALYSIS_PROMPT,
    CLARIFY_PROMPT, CHART_SUGGEST_PROMPT,
    INTENT_CLASSIFY_PROMPT, OVERVIEW_SQL_PROMPT,
)
from app.services.sql.mcp_tool import query_mysql
from app.services.sql.sql_retry import execute_sql_with_repair
from app.services.sql.db_utils import get_db_sync
from app.services.sql.sql_validator import verify_columns_exist, build_table_schemas
from app.services.sql.sql_executor_utils import build_pre_calculated_block
from app.core.llm_client import call_llm, call_llm_stream
from app.services.sql.schema_controller import build_schema_for_prompt

logger = logging.getLogger(__name__)


def extract_sql_from_response(response: str) -> str | None:
    """从 LLM 回复中提取 SQL 语句（处理 markdown 代码块等情况）"""
    # 尝试匹配 ```sql ... ``` 代码块
    pattern = r"```(?:sql)?\s*\n?(.*?)\n?```"
    matches = re.findall(pattern, response, re.DOTALL)
    if matches:
        return matches[0].strip()

    # 尝试匹配 NEED_MORE_INFO
    if response.strip().startswith("NEED_MORE_INFO:"):
        return None

    # 没有任何标记，直接把内容当 SQL
    cleaned = response.strip().strip(";")
    if cleaned.upper().startswith("SELECT"):
        return cleaned

    return None


def get_clarification(question: str, table_info: list[dict], history: list[Message]) -> str | None:
    """
    判断是否需要向用户追问。

    Returns:
        追问问题文本，或 None 表示信息充足
    """
    table_names = ", ".join([t["table_name"] for t in table_info])
    columns_info = build_schema_for_prompt(table_info, question)
    context = "\n".join([f"{m.role}: {m.content}" for m in history[-4:]]) if history else "无"

    prompt = CLARIFY_PROMPT.format(
        user_question=question,
        table_names=table_names,
        columns_info=columns_info,
        context=context,
    )

    messages = [HumanMessage(content=prompt)]
    result = call_llm(messages)
    content = result.content.strip()

    # 如果 LLM 认为信息充足，会输出 "OK" 或类似内容
    if content.upper() in ("OK", "SUFFICIENT", "无需追问", ""):
        return None
    if len(content) < 10:
        return None
    return content


def classify_intent(question: str, table_info: list[dict]) -> str:
    """
    分类用户意图: overview(数据概览) / analysis(业务分析) / clarify(信息不足).

    使用关键词匹配 + LLM 兜底的混合策略：
    1. 关键词规则先匹配最明显的概览/分析意图
    2. 规则无法判断时再调用 LLM

    Returns:
        意图类型字符串
    """
    q = question.strip().lower()

    # === 规则 1: 数据概览关键词 ===
    overview_kw = [
        "基本情况", "概览", "概述", "整体", "介绍", "有什么", "字段",
        "分析一下", "看看数据", "了解一下", "统计信息", "描述", "总览",
        "概况", "数据情况", "基本信息", "这个数据集", "数据集的",
        "整体情况", "全部数据", "所有数据",
    ]
    for kw in overview_kw:
        if kw in q:
            logger.info(f"[classify_intent] keyword match overview: '{kw}' in '{q[:60]}'")
            return "overview"

    # === 规则 2: 业务分析关键词 ===
    analysis_kw = [
        "趋势", "对比", "排名", "哪个最好", "哪个最差", "各月", "按月",
        "分组", "前几", "top", "占比", "最多", "最少", "最高", "最低",
        "变化", "增长", "减少", "上升", "下降", "同比", "环比",
        "销售额", "利润", "成本", "销售", "盈利",
    ]
    for kw in analysis_kw:
        if kw in q:
            logger.info(f"[classify_intent] keyword match analysis: '{kw}' in '{q[:60]}'")
            return "analysis"

    # === 规则 3: LLM 兜底（规则无法判断的模糊情况）===
    columns_info = build_schema_for_prompt(table_info, question)
    prompt = INTENT_CLASSIFY_PROMPT.format(
        user_question=question,
        columns_info=columns_info,
    )
    messages = [HumanMessage(content=prompt)]
    result = call_llm(messages)
    intent = result.content.strip().lower()
    logger.info(f"[classify_intent] LLM fallback: question='{question[:50]}' → raw='{result.content.strip()}' → intent='{intent}'")
    if intent in ("overview", "analysis", "clarify"):
        return intent
    logger.warning(f"[classify_intent] unknown intent '{intent}', defaulting to 'analysis'")
    return "analysis"


def is_field_list_question(question: str) -> bool:
    """检测用户是否只是询问有哪些字段"""
    q = question.strip().lower()
    field_kw = ["有哪些字段", "有什么字段", "字段有哪些", "有哪些数据", "表结构", "有什么列", "所有字段"]
    return any(kw in q for kw in field_kw)


def generate_field_list(table_info: list[dict]) -> str:
    """仅返回字段列表，不输出额外分析"""
    lines = [f"数据集包含以下字段：\n"]
    for t in table_info:
        lines.append(f"表名: {t['table_name']}")
        for c in t.get("columns", []):
            comment = c.get("comment", "")
            comment_str = f" - {comment}" if comment else ""
            lines.append(f"- {c['name']} ({c['type']}){comment_str}")
    return "\n".join(lines)


def _build_analysis_prompt(
    template: str,
    question: str,
    sql: str,
    result_data: list[dict],
    table_name: str,
    max_rows: int,
) -> str:
    """构建分析类 Prompt（概览用 SIMPLE，业务分析用 STRUCTURED）"""
    formatted = json.dumps(result_data[:max_rows], ensure_ascii=False, default=str)
    pre_calculated = build_pre_calculated_block(result_data)
    return template.format(
        user_question=question,
        sql=sql,
        sql_result=formatted,
        table_name=table_name,
        pre_calculated=pre_calculated,
    )


def _stream_analysis_text(prompt: str):
    """
    流式生成分析文本，逐块 yield。

    降级策略：
    - 流尚未产出任何内容就失败 → 降级为非流式 call_llm（可整体重试）
    - 流已产出部分内容后中断 → 补一条中断提示（已输出内容不可撤回）
    """
    messages = [HumanMessage(content=prompt)]
    yielded = False
    try:
        for chunk in call_llm_stream(messages):
            yielded = True
            yield chunk
    except Exception as e:
        if yielded:
            logger.error(f"流式分析中途中断: {e}")
            yield "\n\n（分析生成中断，请重试）"
        else:
            logger.warning(f"流式分析启动失败，降级为非流式: {e}")
            response = call_llm(messages)
            yield response.content.strip()


def _build_error_analysis(exec_result: dict, sql: str) -> str:
    """SQL 执行失败时构建错误说明文本"""
    retry_info = ""
    if exec_result.get("retry_count", 0) > 0:
        retry_info = (
            f"系统已尝试 {exec_result['retry_count']} 次自动修复，"
            f"请尝试更具体地描述您的需求。"
        )
    return (
        f"查询执行出错: {exec_result['error']}\n\n"
        f"原始SQL:\n```sql\n{exec_result.get('final_sql', sql)}\n```\n\n"
        f"{retry_info}"
    )


def _overview_stream(request: QueryRequest, table_info: list[dict]):
    """
    数据概览意图的流式处理：生成描述性统计 SQL → 执行 → 流式分析。

    Yields:
        (event, data) 事件元组，最后一条为 ("result", QueryResponse.model_dump())
    """
    primary_table = table_info[0]["table_name"]
    combined_schema = build_schema_for_prompt(table_info, request.question)

    yield "stage", {"stage": "generating_sql", "label": "正在生成 SQL..."}

    prompt = OVERVIEW_SQL_PROMPT.format(
        table_name=primary_table,
        columns_schema=combined_schema,
        sample_data="(详见上方示例数据)",
        user_question=request.question,
    )

    llm_messages = [
        SystemMessage(content="你是一个专业的 SQL 数据分析助手。严格遵循用户指令输出。"),
        HumanMessage(content=prompt),
    ]

    llm_response = call_llm(llm_messages)
    raw_content = llm_response.content.strip()
    sql = extract_sql_from_response(raw_content)

    if sql is None:
        yield "result", QueryResponse(
            answer=f"无法为数据概览生成 SQL。请尝试更具体的问题。\nLLM 原始回复: {raw_content[:200]}",
            clarification_needed=False,
        ).model_dump()
        return

    # 字段存在性校验
    table_schemas = build_table_schemas(table_info)
    cols_ok, missing_cols = verify_columns_exist(sql, table_schemas, primary_table)
    if not cols_ok:
        actual_cols = table_schemas.get(primary_table, [])
        answer = (
            f"抱歉，当前数据集中没有找到字段：{missing_cols}，无法进行相关分析。"
            f"当前数据集包含的字段有：{actual_cols}"
        )
        logger.warning(f"[overview] 字段校验失败: {missing_cols}, 实际字段: {actual_cols}")
        yield "result", QueryResponse(answer=answer, clarification_needed=False).model_dump()
        return

    # SQL 生成成功 → 立即推给前端展示
    yield "sql", {"sql": sql}
    yield "stage", {"stage": "executing", "label": "正在查询数据..."}

    exec_result = execute_sql_with_repair(
        sql=sql,
        table_info=table_info,
        question=request.question,
    )

    result_data = exec_result.get("data", [])

    # 如果 SQL 被修复过，使用修复后的 SQL 作为最终 SQL
    if exec_result.get("retry_count", 0) > 0:
        sql = exec_result.get("final_sql", sql)
        logger.info(f"[overview] SQL 经 {exec_result['retry_count']} 次修复后成功执行")
        yield "sql", {"sql": sql, "repaired": True}

    if result_data:
        yield "stage", {"stage": "analyzing", "label": "正在生成分析..."}
        analysis_prompt = _build_analysis_prompt(
            SIMPLE_ANALYSIS_PROMPT, request.question, sql, result_data,
            primary_table, max_rows=20,
        )
        parts = []
        for text_chunk in _stream_analysis_text(analysis_prompt):
            parts.append(text_chunk)
            yield "delta", {"text": text_chunk}
        analysis = "".join(parts).strip()
    elif exec_result.get("error"):
        analysis = _build_error_analysis(exec_result, sql)
    else:
        analysis = "查询执行成功，但未返回任何数据。请确认数据集中存在数据。"

    if result_data:
        yield "stage", {"stage": "charting", "label": "正在生成图表..."}
    chart_config = suggest_chart(result_data, request.question)

    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = create_or_get_conversation(1, request.dataset_ids)

    save_message(conversation_id, "user", request.question)
    metadata = getattr(llm_response, "response_metadata", {}) or {}
    total_tokens = (metadata.get("token_usage", {}) or {}).get("total_tokens", 0)
    msg_id = save_message(
        conversation_id=conversation_id,
        role="assistant",
        content=analysis,
        sql=sql,
        sql_result=result_data[:200] if result_data else None,
        chart_config=chart_config,
        token_usage=total_tokens,
    )
    update_mcp_logs_with_message_id(msg_id)

    yield "result", QueryResponse(
        message_id=msg_id,
        sql=sql,
        result=result_data[:500] if result_data else [],
        chart_config=chart_config,
        answer=analysis,
        token_usage=total_tokens,
        clarification_needed=False,
    ).model_dump()


def suggest_chart(result_data: list[dict], question: str) -> ChartConfig | None:
    """推荐图表配置"""
    if not result_data:
        return None

    fields = list(result_data[0].keys())
    prompt = CHART_SUGGEST_PROMPT.format(
        sql_result_fields=", ".join(fields),
        row_count=len(result_data),
        user_question=question,
    )
    messages = [HumanMessage(content=prompt)]
    response = call_llm(messages)
    content = response.content.strip()

    try:
        # 尝试解析 JSON
        pattern = r"\{.*\}"
        match = re.search(pattern, content, re.DOTALL)
        if match:
            config = json.loads(match.group())
            return ChartConfig(
                type=ChartType(config.get("type", "table")),
                x_field=config.get("x_field"),
                y_field=config.get("y_field"),
                category_field=config.get("category_field"),
                value_field=config.get("value_field"),
                title=config.get("title", question),
            )
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"图表推荐 JSON 解析失败: {e}, 原始内容: {content[:100]}")

    return infer_chart_type_simple(fields, result_data, question)


def infer_chart_type_simple(fields: list[str], data: list[dict], question: str) -> ChartConfig:
    """基于字段类型的简单图表推断（备选方案）"""
    if len(fields) < 2:
        return ChartConfig(type=ChartType.table, title=question)

    text_fields = []
    numeric_fields = []
    date_fields = []

    for f in fields:
        sample_vals = [row.get(f) for row in data[:10] if row.get(f) is not None]
        if not sample_vals:
            continue
        if all(isinstance(v, (int, float)) for v in sample_vals):
            numeric_fields.append(f)
        elif any(isinstance(v, str) and re.match(r"\d{4}[-/]\d{1,2}", str(v)) for v in sample_vals):
            date_fields.append(f)
        else:
            text_fields.append(f)

    x_field = date_fields[0] if date_fields else (text_fields[0] if text_fields else fields[0])
    y_field = numeric_fields[0] if numeric_fields else (fields[-1] if fields[-1] != x_field else fields[0])

    if date_fields and numeric_fields:
        chart_type = ChartType.line
    elif text_fields and numeric_fields:
        if len(data) <= 10:
            chart_type = ChartType.pie if y_field else ChartType.bar
        else:
            chart_type = ChartType.bar
    else:
        chart_type = ChartType.table

    return ChartConfig(
        type=chart_type,
        x_field=x_field,
        y_field=y_field,
        title=question,
    )


def save_message(
    conversation_id: int,
    role: str,
    content: str,
    sql: str | None = None,
    sql_result: list[dict] | None = None,
    chart_config: ChartConfig | None = None,
    token_usage: int = 0,
) -> int:
    """
    保存消息到 messages 表。

    Returns:
        新插入的消息 ID
    """
    db = get_db_sync()
    try:
        sql_stmt = text("""
            INSERT INTO messages (conversation_id, role, content, sql_generated,
                                   sql_result, chart_config, chart_type, token_usage, status)
            VALUES (:conversation_id, :role, :content, :sql_generated,
                    :sql_result, :chart_config, :chart_type, :token_usage, :status)
        """)
        result = db.execute(sql_stmt, {
            "conversation_id": conversation_id,
            "role":            role,
            "content":         content,
            "sql_generated":   sql,
            "sql_result":      json.dumps(sql_result, ensure_ascii=False, default=str) if sql_result else None,
            "chart_config":    chart_config.model_dump_json() if chart_config else None,
            "chart_type":      chart_config.type.value if chart_config else None,
            "token_usage":     token_usage,
            "status":          1,
        })
        db.commit()
        return result.lastrowid
    except Exception as e:
        db.rollback()
        logger.error(f"保存消息失败: {e}")
        raise
    finally:
        db.close()


def update_mcp_logs_with_message_id(message_id: int, limit: int = 10):
    """回填最近未关联 message_id 的 MCP 日志（含 SQL 修复重试产生的额外日志）"""
    db = get_db_sync()
    try:
        sql = text("""
            UPDATE mcp_logs SET message_id = :message_id
            WHERE message_id IS NULL
            ORDER BY id DESC LIMIT :limit
        """)
        db.execute(sql, {"message_id": message_id, "limit": limit})
        db.commit()
    except Exception as e:
        logger.error(f"回填 MCP 日志 message_id 失败: {e}")
        db.rollback()
    finally:
        db.close()


def create_or_get_conversation(user_id: int, dataset_ids: list[int], title: str = "新对话") -> int:
    """
    创建新会话或返回已有会话（简化实现：总是创建新会话）。

    实际生产环境应调用后端 API 或通过 user_id + dataset_ids 匹配已有会话。
    """
    db = get_db_sync()
    try:
        sql = text("""
            INSERT INTO conversations (title, user_id, dataset_ids, status)
            VALUES (:title, 1, :dataset_ids, 1)
        """)
        result = db.execute(sql, {
            "title": title,
            "dataset_ids": json.dumps(dataset_ids, ensure_ascii=False),
        })
        db.commit()
        return result.lastrowid
    except Exception as e:
        db.rollback()
        logger.error(f"创建会话失败: {e}")
        raise
    finally:
        db.close()


def load_conversation_history(
    conversation_id: int, max_messages: int = 20
) -> list[Message]:
    """
    加载会话历史消息。

    Args:
        conversation_id: 会话 ID
        max_messages: 最多加载条数

    Returns:
        Message 列表
    """
    db = get_db_sync()
    try:
        sql = text("""
            SELECT role, content, sql_generated, chart_config
            FROM messages
            WHERE conversation_id = :conversation_id AND status = 1
            ORDER BY created_at ASC
            LIMIT :max_messages
        """)
        result = db.execute(sql, {
            "conversation_id": conversation_id,
            "max_messages":    max_messages,
        })
        history = []
        for row in result:
            chart = None
            if row.chart_config:
                try:
                    chart_data = json.loads(row.chart_config)
                    chart = ChartConfig(**chart_data)
                except (json.JSONDecodeError, TypeError):
                    pass
            history.append(Message(
                role=row.role,
                content=row.content or "",
                sql=row.sql_generated,
                chart_config=chart,
            ))
        return history
    finally:
        db.close()


# =============================================================================
# 最终 SQL 独立校验
# =============================================================================

def _verify_final_result(
    sql: str | None,
    result_data: list[dict] | None,
    exec_result: dict[str, Any],
) -> None:
    """
    在返回最终结果前，对 SQL 做一次独立校验。

    不重新执行 SQL（避免重复查询），但做结构级检查：
    1. SQL 非空且通过安全校验
    2. 如果 exec_result 标记为成功但 result_data 为空，发出警告
    3. 如果 exec_result 标记为失败但 result_data 非空，标记为不一致
    """
    if not sql:
        logger.warning("[verify] 最终结果中 SQL 为空，可能 LLM 未正确生成")
        return

    # 1) 安全校验
    try:
        from app.services.sql.db_utils import validate_sql_safe
        validate_sql_safe(sql)
    except ValueError as e:
        logger.error("[verify] 最终 SQL 未通过安全校验: %s", e)
        return

    # 2) 结果一致性检查
    has_error = bool(exec_result.get("error"))
    has_data = bool(result_data)

    if has_error and has_data:
        logger.warning(
            "[verify] 不一致：exec_result 有错误但 result_data 非空 "
            "(error=%s, rows=%d)",
            exec_result.get("error", "")[:100],
            len(result_data or []),
        )
    elif not has_error and not has_data and exec_result.get("retry_count", 0) == 0:
        logger.info(
            "[verify] SQL 执行成功但无数据返回 (sql=%s)",
            sql[:100],
        )


def generate_and_execute_stream(request: QueryRequest):
    """
    Text-to-SQL 主流程（流式版本）。

    以 (event, data) 元组形式逐步产出事件，供 SSE 接口推送：
    - ("stage", {"stage", "label"})   阶段进度
    - ("sql",   {"sql", "repaired?"}) SQL 生成/修复后立即推送
    - ("delta", {"text"})             分析文本增量（打字机）
    - ("result", QueryResponse dict)  最终完整结果（唯一成功终止标志）

    Args:
        request: QueryRequest 包含问题、数据集、历史等
    """
    user_id = 1  # 简化：传 1，生产环境从 token 获取

    yield "stage", {"stage": "understanding", "label": "正在理解问题..."}

    # Step 1: 查询表信息
    table_info = list_user_tables(request.dataset_ids)
    if not table_info:
        yield "result", QueryResponse(
            answer="所选数据集中未找到有效的数据表，请先上传文件。",
            clarification_needed=False,
        ).model_dump()
        return

    primary_table = table_info[0]["table_name"]

    # Step 2: 构建 Schema 描述（智能排序 + Token 控制）
    combined_schema = build_schema_for_prompt(table_info, request.question)

    # Step 3: 检测"纯问字段" → 只返回字段列表
    if is_field_list_question(request.question):
        yield "result", QueryResponse(
            answer=generate_field_list(table_info),
            clarification_needed=False,
        ).model_dump()
        return

    # Step 4: 意图分类
    intent = classify_intent(request.question, table_info)
    logger.info(f"[generate_and_execute] intent='{intent}' question='{request.question[:60]}'")

    # Step 4a: 数据概览 → 走专用概览流程
    if intent == "overview":
        logger.info("→ 进入 overview 概览流程")
        yield from _overview_stream(request, table_info)
        return

    # Step 4b: 信息不足 → 追问
    if intent == "clarify":
        clarification = get_clarification(request.question, table_info, request.history)
        if clarification:
            yield "result", QueryResponse(
                answer=clarification,
                clarification_needed=True,
            ).model_dump()
            return

    # Step 4c: 业务分析 → 跳过追问，直接生成 SQL（意图分类已确认可回答）

    # Step 5: 处理历史上下文
    history_context = ""
    if request.history:
        history_parts = []
        for msg in request.history[-6:]:
            history_parts.append(f"{msg.role}: {msg.content}")
        history_context = "历史对话:\n" + "\n".join(history_parts)

    if not history_context:
        history_context = "无历史对话，这是本会话中的第一个问题。"

    # Step 6: 调用 DeepSeek 生成 SQL
    yield "stage", {"stage": "generating_sql", "label": "正在生成 SQL..."}

    text_to_sql_prompt = TEXT_TO_SQL_PROMPT.format(
        table_name=primary_table,
        columns_schema=combined_schema,
        sample_data="(详见上方示例数据)",
        history_context=history_context,
        user_question=request.question,
    )

    llm_messages = [
        SystemMessage(content="你是一个专业的 SQL 数据分析助手。严格遵循用户指令输出。"),
        HumanMessage(content=text_to_sql_prompt),
    ]

    llm_response = call_llm(llm_messages)
    raw_content = llm_response.content.strip()

    sql = extract_sql_from_response(raw_content)

    # Step 7: 如果 LLM 没有生成有效 SQL，返回错误
    if sql is None:
        # 可能是 NEED_MORE_INFO
        if raw_content.startswith("NEED_MORE_INFO:"):
            yield "result", QueryResponse(
                answer=raw_content.replace("NEED_MORE_INFO:", "").strip(),
                clarification_needed=True,
            ).model_dump()
            return
        yield "result", QueryResponse(
            answer=f"无法从您的问题中生成有效的 SQL 查询。请尝试更明确地描述您需要分析什么。\n\nLLM 原始回复: {raw_content[:200]}",
            clarification_needed=False,
        ).model_dump()
        return

    # Step 8: 字段存在性校验（在 SQL 执行前）
    table_schemas = build_table_schemas(table_info)
    cols_ok, missing_cols = verify_columns_exist(sql, table_schemas, primary_table)
    if not cols_ok:
        actual_cols = table_schemas.get(primary_table, [])
        answer = (
            f"抱歉，当前数据集中没有找到字段：{missing_cols}，无法进行相关分析。"
            f"当前数据集包含的字段有：{actual_cols}"
        )
        logger.warning(f"[generate_and_execute] 字段校验失败: {missing_cols}, 实际字段: {actual_cols}")
        yield "result", QueryResponse(answer=answer, clarification_needed=False).model_dump()
        return

    # SQL 生成并校验通过 → 立即推给前端展示
    yield "sql", {"sql": sql}
    yield "stage", {"stage": "executing", "label": "正在查询数据..."}

    # Step 9: 通过 MCP Tool 执行 SQL（含自动修复重试）
    exec_result = execute_sql_with_repair(
        sql=sql,
        table_info=table_info,
        question=request.question,
    )

    result_data = exec_result.get("data", [])

    # 如果 SQL 被修复过，使用修复后的 SQL
    if exec_result.get("retry_count", 0) > 0:
        sql = exec_result.get("final_sql", sql)
        logger.info(
            f"[generate_and_execute] SQL 经 {exec_result['retry_count']} 次修复后成功执行"
        )
        yield "sql", {"sql": sql, "repaired": True}
        yield "stage", {
            "stage": "repaired",
            "label": f"SQL 已自动修复（重试 {exec_result['retry_count']} 次）",
        }

    # Step 10: 生成结构化业务分析报告（流式输出）
    if result_data:
        yield "stage", {"stage": "analyzing", "label": "正在生成分析..."}
        analysis_prompt = _build_analysis_prompt(
            STRUCTURED_ANALYSIS_PROMPT, request.question, sql, result_data,
            primary_table, max_rows=50,
        )
        parts = []
        for text_chunk in _stream_analysis_text(analysis_prompt):
            parts.append(text_chunk)
            yield "delta", {"text": text_chunk}
        analysis = "".join(parts).strip()
    else:
        if exec_result.get("error"):
            analysis = _build_error_analysis(exec_result, sql)
        else:
            analysis = "查询执行成功，但未返回任何数据。请确认数据集中存在符合条件的记录。"

    # Step 11: 推荐图表类型
    if result_data:
        yield "stage", {"stage": "charting", "label": "正在生成图表..."}
    chart_config = suggest_chart(result_data, request.question)

    # ── 最终 SQL 独立校验 ──
    _verify_final_result(sql, result_data, exec_result)

    # Step 12: 创建会话并保存消息
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = create_or_get_conversation(user_id, request.dataset_ids)

    # 保存用户消息
    save_message(conversation_id, "user", request.question)

    # 保存 AI 消息
    metadata = getattr(llm_response, "response_metadata", {}) or {}
    total_tokens = (metadata.get("token_usage", {}) or {}).get("total_tokens", 0)

    msg_id = save_message(
        conversation_id=conversation_id,
        role="assistant",
        content=analysis,
        sql=sql,
        sql_result=result_data[:200] if result_data else None,
        chart_config=chart_config,
        token_usage=total_tokens,
    )

    # 回填 MCP 日志的 message_id
    update_mcp_logs_with_message_id(msg_id)

    yield "result", QueryResponse(
        message_id=msg_id,
        sql=sql,
        result=result_data[:500] if result_data else [],
        chart_config=chart_config,
        answer=analysis,
        token_usage=total_tokens,
        clarification_needed=False,
    ).model_dump()


def generate_and_execute(
    request: QueryRequest,
) -> QueryResponse:
    """
    Text-to-SQL 主流程（非流式兼容版本，供旧接口 /ai/query 使用）。

    内部消费流式版本的事件，只取最终 result，避免维护两套逻辑。

    Args:
        request: QueryRequest 包含问题、数据集、历史等

    Returns:
        QueryResponse 包含 SQL、结果、图表、结论
    """
    final: dict | None = None
    for event, data in generate_and_execute_stream(request):
        if event == "result":
            final = data
    if final is None:
        raise RuntimeError("查询流程未产生最终结果")
    return QueryResponse(**final)
