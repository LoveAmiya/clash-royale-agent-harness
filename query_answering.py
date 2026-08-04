import json
import logging
import asyncio
import time
from dataclasses import dataclass, field

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel, OpenAIResponseModel

from answer_builder import build_retrieval_query
from app_config import (
    COMPRESS_CHAR_BUDGET,
    COMPRESS_MAX_ITEMS,
    META_COMPRESS_CHAR_BUDGET,
    META_COMPRESS_MAX_ITEMS,
    META_RERANK_TOP_N,
    META_RETRIEVAL_LANE_TOP_K,
    RERANK_TOP_N,
    RETRIEVAL_ALPHA,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    OPENAI_CLIENT_KWARGS,
    OPENAI_MODEL,
    OPENAI_REVIEW_MODEL,
    OPENAI_REASONING_EFFORT,
    SYNTHESIS_REASONING_EFFORT,
    OPENAI_WIRE_API,
    MODEL_CALL_TIMEOUT_SECONDS,
    EXTERNAL_API_REQUIRED,
    RAG_FACT_VALIDATION_ENABLED,
)
from harness.executor import SkillExecutor
from hybrid_retriever import HybridRetriever
from model_gateway import generate_model_text, generate_model_text_stream, uses_responses_api
from rag_quality import GroundedStreamBuffer, GroundingValidationError, validate_answer_grounding
from runtime_events import RuntimeEventEmitter
from planner.planner import RuleBasedPlanner
from query_parser import extract_text_content, subquery_semantic_key
from retrieval_postprocess import (
    build_context_and_refs,
    compress_results,
    rerank_results,
    select_diverse_results,
    strip_generated_reference_section,
)
from skills.base import SkillContext
from skills.meta_evidence import build_meta_evidence_pack
from skills.registry import build_default_registry


logger = logging.getLogger(__name__)
FALLBACK_CONTENT_INTERVAL_SECONDS = 0.12
META_EVIDENCE_LANES = ("archetype", "deck_profile", "card_pair")
DATA_ANALYSIS_SYSTEM_PROMPT = (
    "你是皇室战争数据分析助手，使用中文回答。\n"
    "所有卡牌名必须使用系统标准中文名，不得直接输出英文卡牌名。\n"
    "输出必须是适合纯文本前端的自然中文：不要使用 Markdown 标题井号、星号粗体或英文占位标题。\n"
    "数据结论只能来自提供的结构化证据或当前快照 RAG 检索证据，不得虚构统计、日期、来源、卡组或对手信息。\n"
    "涉及使用率、胜率、场次等数字时必须原样引用证据，不得自行四舍五入、改写精度或推算新数值。\n"
    "可以给出由使用率、胜率、样本量、常见搭配和对阵数据支持的配卡分析，但必须明确区分观测与推断。\n"
    "当前数据不支持未来预测、精确概率或因果效果；只有检索到同口径 meta_delta 证据时才可报告相邻七天分段的观测变化，不能用当前排名或样本比例冒充历史趋势。\n"
    "不得提供战队赛赛程或战队备战建议。不得提供具体打法；问题超出数据证据时要清楚说明边界。\n"
    "不要展示内部推理过程。"
)


class RequiredExternalAPIError(RuntimeError):
    """Raised when strict mode forbids a local substitute for an API result."""


@dataclass(slots=True)
class AnswerResult:
    answer: str
    trace_id: str | None
    parsed: dict
    plan: dict | None
    selected_skill: str | None
    mode: str | None
    metadata: dict
    sub_results: list[dict] = field(default_factory=list)


def build_reviewer_model(api_key: str) -> OpenAIChatModel | OpenAIResponseModel:
    common_kwargs = {
        "model_name": OPENAI_REVIEW_MODEL,
        "api_key": api_key,
        "stream": False,
        "client_kwargs": OPENAI_CLIENT_KWARGS,
    }
    if OPENAI_WIRE_API == "responses":
        return OpenAIResponseModel(
            **common_kwargs,
            reasoning_effort=OPENAI_REASONING_EFFORT,
        )
    if OPENAI_WIRE_API == "chat_completions":
        return OpenAIChatModel(
            **common_kwargs,
            reasoning_effort=OPENAI_REASONING_EFFORT,
        )
    raise ValueError(f"Unsupported OPENAI_WIRE_API: {OPENAI_WIRE_API}")


def build_snapshot_fallback_answer(
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
) -> str:
    """模型不可用时，直接整理快照事实，不把本地榜单伪装成实时 Meta 推演。"""
    top_decks = sorted(top_decks_data, key=lambda item: item.get("rank", 10**9))[:5]
    top_cards = sorted(
        cards_meta_data,
        key=lambda item: float(item.get("usage_rate", 0) or 0),
        reverse=True,
    )[:5]

    deck_lines = [
        f"- 第 {item.get('rank')} 名：{item.get('deck_name', '未命名卡组')}（平均费用 {item.get('avg_elixir', '未知')}）"
        for item in top_decks
    ]
    card_lines = [
        f"- {item.get('card_name', '未命名卡牌')}：使用率 {item.get('usage_rate', '未知')}%，胜率 {item.get('win_rate', '未知')}%"
        for item in top_cards
    ]

    return (
        "模型服务暂不可用，以下是基于本地数据快照的直接整理，不是 LLM 的策略推演。\n\n"
        "本地排行榜前五卡组：\n"
        + ("\n".join(deck_lines) if deck_lines else "- 当前没有可用的卡组快照。")
        + "\n\n"
        "快照中使用率靠前的卡牌：\n"
        + ("\n".join(card_lines) if card_lines else "- 当前没有可用的卡牌快照。")
        + "\n\n"
        "数据边界：该项目保存的是排行榜和单卡静态快照，不含全量卡组使用率分布、版本更新时间或实时对局样本；"
        "因此可以展示榜单前列构筑，但不能严谨断言它们就是整个实时环境中占比最高的流派。"
    )


def retrieve_meta_candidates(
    retriever: HybridRetriever,
    query: str,
    *,
    dataset_scope: str | None,
    deck_mode: str | None,
    entity_mode: str | None,
    source_type: str | None = None,
) -> tuple[list[dict], list[str]]:
    """Recall global and typed evidence lanes, deduplicated by stable document ID."""
    lane_types: tuple[str | None, ...] = (source_type,) if source_type else (None, *META_EVIDENCE_LANES)
    merged: dict[str, dict] = {}
    lanes: list[str] = []
    for lane_type in lane_types:
        lane_name = lane_type or "global"
        lane_results = retriever.hybrid_search(
            query=query,
            top_k_bm25=RETRIEVAL_TOP_K_BM25,
            top_k_dense=RETRIEVAL_TOP_K_DENSE,
            final_top_k=(
                RETRIEVAL_FINAL_TOP_K if lane_type is None else META_RETRIEVAL_LANE_TOP_K
            ),
            alpha=RETRIEVAL_ALPHA,
            source_type=lane_type,
            dataset_scope=dataset_scope,
            deck_mode=deck_mode,
            entity_mode=entity_mode,
        )
        lanes.append(lane_name)
        for item in lane_results:
            doc_id = str(item.get("doc", {}).get("doc_id") or "")
            if not doc_id:
                continue
            previous = merged.get(doc_id)
            if previous is None or float(item.get("final_score", 0.0)) > float(previous.get("final_score", 0.0)):
                merged[doc_id] = item
    return list(merged.values()), lanes


def build_retrieved_evidence_fallback(compressed_results: list[dict]) -> str:
    """Expose retrieved current-scope evidence when synthesis times out."""
    evidence_lines = []
    for item in compressed_results:
        doc = item.get("doc") if isinstance(item, dict) else None
        if not isinstance(doc, dict):
            continue
        text = str(item.get("compressed_text") or doc.get("text") or "").strip()
        if not text:
            continue
        title = str(doc.get("metadata", {}).get("title") or doc.get("source_type") or "检索证据")
        evidence_lines.append(f"- {title}：{text}")
    return (
        "模型综合请求超时，已保留本次检索到的当前数据范围证据。"
        "以下内容是证据原文摘要，未进行额外推演：\n"
        + ("\n".join(evidence_lines) if evidence_lines else "- 当前没有可显示的检索证据。")
    )


async def build_rag_answer(
    user_text: str,
    parsed: dict,
    retriever: HybridRetriever,
    source_type: str,
    reviewer_model: OpenAIChatModel,
    api_key: str = "",
    metadata: dict | None = None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = True,
) -> str:
    retrieval_query = build_retrieval_query(parsed, user_text)
    subquery_id = str((metadata or {}).get("subquery_id") or "q")

    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.retrieve",
            phase="retrieve",
            status="running",
            subquery_id=subquery_id,
            title="正在检索 RAG 证据",
            detail="从当前快照检索与问题相关的证据文档。",
        )

    results = retriever.hybrid_search(
        query=retrieval_query,
        top_k_bm25=RETRIEVAL_TOP_K_BM25,
        top_k_dense=RETRIEVAL_TOP_K_DENSE,
        final_top_k=RETRIEVAL_FINAL_TOP_K,
        alpha=RETRIEVAL_ALPHA,
        source_type=source_type,
        dataset_scope=(metadata or {}).get("dataset_scope"),
        deck_mode=(metadata or {}).get("deck_mode"),
        entity_mode=(metadata or {}).get("entity_mode"),
    )
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.retrieve",
            phase="retrieve",
            status="completed",
            subquery_id=subquery_id,
            title="已检索 RAG 证据",
            detail=f"找到 {len(results)} 条候选证据。",
        )

    if not results:
        return "我不知道，当前数据里没有检索到足够相关的信息。\n参考来源：无"

    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="running",
            subquery_id=subquery_id,
            title="正在重排序证据",
            detail="按问题相关性对候选证据进行确定性重排序。",
        )
    reranked = rerank_results(
        question=user_text,
        parsed=parsed,
        results=results,
        top_n=RERANK_TOP_N,
    )
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="completed",
            subquery_id=subquery_id,
            title="已重排序证据",
            detail=f"保留 {len(reranked)} 条高相关候选证据。",
        )

    if not reranked:
        return "我不知道，当前数据里没有足够高相关的候选结果。\n参考来源：无"

    compressed = compress_results(
        reranked,
        max_items=COMPRESS_MAX_ITEMS,
        char_budget=COMPRESS_CHAR_BUDGET,
    )

    if not compressed:
        return "我不知道，当前数据里没有足够可用的压缩上下文。\n参考来源：无"

    retrieved_context, refs_text = build_context_and_refs(compressed)
    allowed_doc_ids = {str(item["doc"].get("doc_id")) for item in compressed}
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.review",
            phase="review",
            status="completed",
            subquery_id=subquery_id,
            title="已审查证据边界",
            detail=f"确认 {len(allowed_doc_ids)} 条可引用证据并锁定引用范围。",
        )

    reviewer_instructions = """你是一个严格基于检索证据回答问题的助手。
规则：
1. 只能根据提供的检索证据回答。
2. 如果证据不足，必须明确说“我不知道”或“当前数据里没有”。
3. 回答要简洁清楚，末尾必须保留“参考来源：”部分。
4. 不要输出推理过程。"""
    reviewer_agent = ReActAgent(
        name="Reviewer",
        sys_prompt=(
            "你是一个严格基于检索证据回答问题的助手。\n"
            "规则：\n"
            "1. 只能根据提供的检索证据回答。\n"
            "2. 如果证据不足，必须明确说“我不知道”或“当前数据里没有”。\n"
            "3. 回答要简洁清楚。\n"
            "4. 回答末尾必须保留“参考来源：”部分。\n"
            "5. 不要输出推理过程。"
        ),
        model=reviewer_model,
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )
    reviewer_agent.set_console_output_enabled(enabled=False)

    grounded_question = (
        f"用户问题：{user_text}\n\n"
        f"结构化参数：{json.dumps(parsed, ensure_ascii=False)}\n\n"
        "下面是经过召回、重排序和压缩后的候选证据，请严格基于这些证据作答：\n\n"
        f"{retrieved_context}\n\n"
        f"请在回答末尾附上：\n参考来源：\n{refs_text}\n\n"
        "如果这些证据仍不足以回答问题，请明确说“我不知道”或“当前数据里没有”。"
    )

    grounded_msg = Msg(name="user", role="user", content=grounded_question)
    try:
        if uses_responses_api() and stream_content and event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.generate",
                phase="generate",
                status="running",
                subquery_id=subquery_id,
                title="正在生成检索结论",
                detail="模型正在基于已检索证据输出公开文本。",
            )
            chunks: list[str] = []
            stream_buffer = GroundedStreamBuffer(retrieved_context, allowed_doc_ids)
            try:
                async with asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS):
                    async for delta in generate_model_text_stream(
                        api_key=api_key,
                        instructions=reviewer_instructions,
                        input_text=grounded_question,
                        reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                    ):
                        chunks.append(delta)
                        for validated_chunk in stream_buffer.push(delta):
                            await event_sink.content(validated_chunk, delta=True)
                for validated_chunk in stream_buffer.finish():
                    await event_sink.content(validated_chunk, delta=True)
                answer = "".join(chunks).strip()
                if not answer:
                    raise RuntimeError("model stream returned no public text")
            except Exception:
                # A failed stream with no public text can safely use the
                # non-streaming endpoint. Never append a second answer after
                # a partially delivered stream.
                if chunks:
                    raise
                if metadata is not None:
                    metadata["model_stream"] = "fallback_chunked"
                await event_sink.execution(
                    step_id=f"{subquery_id}.generate",
                    phase="generate",
                    status="running",
                    subquery_id=subquery_id,
                    title="模型未提供 token 流",
                    detail="正在输出同一模型已完成的结果分段。",
                )
                answer = await asyncio.wait_for(
                    generate_model_text(
                        api_key=api_key,
                        instructions=reviewer_instructions,
                        input_text=grounded_question,
                        reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                    ),
                    timeout=MODEL_CALL_TIMEOUT_SECONDS,
                )
            else:
                if allowed_doc_ids and not any(doc_id in answer for doc_id in allowed_doc_ids):
                    reference_suffix = f"\n\n参考来源：\n{refs_text}"
                    answer += reference_suffix
                    await event_sink.content(reference_suffix, delta=True)
                await event_sink.execution(
                    step_id=f"{subquery_id}.validate",
                    phase="validate",
                    status="running",
                    subquery_id=subquery_id,
                    title="正在校验回答",
                    detail="检查引用标识和数值事实是否受证据支持。",
                )
                validation = validate_answer_grounding(
                    answer,
                    retrieved_context,
                    allowed_doc_ids,
                    raise_on_failure=RAG_FACT_VALIDATION_ENABLED and EXTERNAL_API_REQUIRED,
                )
                if metadata is not None:
                    metadata["model_stream"] = "streaming"
                    metadata["grounding_validation"] = validation
                await event_sink.execution(
                    step_id=f"{subquery_id}.validate",
                    phase="validate",
                    status="completed",
                    subquery_id=subquery_id,
                    title="回答校验通过",
                    detail="引用和数值事实均通过证据边界校验。",
                )
                await event_sink.execution(
                    step_id=f"{subquery_id}.generate",
                    phase="generate",
                    status="completed",
                    subquery_id=subquery_id,
                    title="已生成检索结论",
                    detail="结论已限制在检索证据范围内。",
                )
                return answer
        if uses_responses_api():
            if not api_key:
                return "当前无法调用模型完成检索综合，请先设置 OPENAI_API_KEY 后重试。\n参考来源：\n" + refs_text
            answer = await asyncio.wait_for(
                generate_model_text(
                    api_key=api_key,
                    instructions=reviewer_instructions,
                    input_text=grounded_question,
                    reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                ),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
            if metadata is not None:
                metadata["model_stream"] = "fallback_chunked"
        else:
            result = await asyncio.wait_for(reviewer_agent(grounded_msg), timeout=MODEL_CALL_TIMEOUT_SECONDS)
            answer = extract_text_content(result)
            if metadata is not None:
                metadata["model_stream"] = "fallback_chunked"
        if allowed_doc_ids and not any(doc_id in answer for doc_id in allowed_doc_ids):
            answer = f"{answer}\n\n参考来源：\n{refs_text}"
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.validate",
                phase="validate",
                status="running",
                subquery_id=subquery_id,
                title="正在校验回答",
                detail="检查引用标识和数值事实是否受证据支持。",
            )
        validation = validate_answer_grounding(
            answer,
            retrieved_context,
            allowed_doc_ids,
            raise_on_failure=RAG_FACT_VALIDATION_ENABLED and EXTERNAL_API_REQUIRED,
        )
        if metadata is not None:
            metadata["grounding_validation"] = validation
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.validate",
                phase="validate",
                status="completed",
                subquery_id=subquery_id,
                title="回答校验通过",
                detail="引用和数值事实均通过证据边界校验。",
            )
        if event_sink is not None and stream_content:
            chunks = [answer[start : start + 80] for start in range(0, len(answer), 80)]
            for index, chunk in enumerate(chunks):
                await event_sink.content(chunk, delta=True)
                if index < len(chunks) - 1:
                    await asyncio.sleep(FALLBACK_CONTENT_INTERVAL_SECONDS)
            await event_sink.execution(
                step_id=f"{subquery_id}.generate",
                phase="generate",
                status="completed",
                subquery_id=subquery_id,
                title="已生成检索结论",
                detail="结论已限制在检索证据范围内。",
            )
        return answer
    except asyncio.TimeoutError:
        if metadata is not None:
            metadata["model_stream"] = "unavailable"
        return "模型在限定时间内未返回，未生成不可靠结论。请检查 OPENAI_MODEL、网络或稍后重试。\n参考来源：\n" + refs_text
    except GroundingValidationError as exc:
        if metadata is not None:
            metadata["model_stream"] = "unavailable"
            metadata["grounding_validation_error"] = str(exc)[:1000]
        logger.warning("rag grounding validation failed detail=%s", str(exc)[:1000])
        return "检索结论未通过引用或数值事实校验，因此未输出不可靠回答。\n参考来源：\n" + refs_text
    except Exception:
        if metadata is not None:
            metadata["model_stream"] = "unavailable"
        # 第三方模型 SDK 的超时不一定继承 asyncio.TimeoutError，必须在 Skill 内降级。
        logger.exception("rag reviewer model call failed")
        return (
            "模型调用失败，未生成不可靠结论。请检查 OPENAI_API_KEY、OPENAI_MODEL、网络或服务商状态后重试。"
            "\n参考来源：\n"
            + refs_text
        )


async def build_evidence_synthesis_answer(
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever,
    api_key: str,
    metadata: dict | None = None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = True,
) -> str:
    """通过 RAG 检索和静态快照完成可追溯的开放问题综合。"""
    # Clan-war data is outside the active product boundary. Meta synthesis is
    # grounded only in official game-data evidence.
    evidence_schedule_data = []
    evidence, sources = build_meta_evidence_pack(
        evidence_schedule_data,
        top_decks_data,
        cards_meta_data,
        include_schedule=False,
    )
    # Strict production retrieval is generated from the active official daily
    # snapshot. It intentionally has no static strategy or stale snapshot set.
    retrieval_source_type = "meta_delta" if parsed.get("analysis_type") == "meta_delta" else None
    subquery_id = str((metadata or {}).get("subquery_id") or "q")
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.retrieve",
            phase="retrieve",
            status="running",
            subquery_id=subquery_id,
            title="正在检索环境证据",
            detail="使用当前官方快照 RAG 检索相关证据。",
        )
    results, retrieval_lanes = retrieve_meta_candidates(
        retriever,
        user_text,
        dataset_scope=(metadata or {}).get("dataset_scope"),
        deck_mode=(metadata or {}).get("deck_mode"),
        entity_mode=(metadata or {}).get("entity_mode"),
        source_type=retrieval_source_type,
    )
    if parsed.get("analysis_type") == "meta_delta":
        selected_scope = str((metadata or {}).get("dataset_scope") or "")
        results = [
            item
            for item in results
            if str(item.get("doc", {}).get("metadata", {}).get("baseline_scope") or "")
            and str(item.get("doc", {}).get("metadata", {}).get("baseline_scope")) != selected_scope
        ]
    if metadata is not None:
        metadata["retrieval_mode"] = results[0].get("retrieval_mode", "none") if results else "none"
        metadata["retrieved_doc_ids"] = [item["doc"].get("doc_id") for item in results]
        metadata["retrieval_source_type"] = retrieval_source_type
        metadata["retrieval_lanes"] = retrieval_lanes
        metadata["retrieval_source_counts"] = {
            source: sum(
                str(item.get("doc", {}).get("source_type") or "unknown") == source
                for item in results
            )
            for source in sorted({
                str(item.get("doc", {}).get("source_type") or "unknown")
                for item in results
            })
        }
        metadata["synthesis_reasoning_effort"] = SYNTHESIS_REASONING_EFFORT
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.retrieve",
            phase="retrieve",
            status="completed",
            subquery_id=subquery_id,
            title="已检索环境证据",
            detail=f"找到 {len(results)} 条候选证据，正在生成受证据约束的回答。",
        )
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="running",
            subquery_id=subquery_id,
            title="正在重排序环境证据",
            detail="按问题相关性对环境候选证据进行确定性重排序。",
        )
    reranked_candidates = rerank_results(
        user_text,
        parsed,
        results,
        top_n=max(len(results), META_RERANK_TOP_N),
    )
    reranked = select_diverse_results(
        reranked_candidates,
        top_n=META_RERANK_TOP_N,
        per_source_limit=3,
    )
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="completed",
            subquery_id=subquery_id,
            title="已重排序环境证据",
            detail=f"保留 {len(reranked)} 条高相关候选证据。",
        )
    compressed = compress_results(
        reranked,
        max_items=META_COMPRESS_MAX_ITEMS,
        char_budget=META_COMPRESS_CHAR_BUDGET,
    )
    if not compressed:
        return (
            "当前知识库没有检索到足够相关的证据，因此不生成策略性结论。"
            "请补充对应的卡组、对手或战术资料后重试。\n\n参考来源：\n"
            f"{sources}"
        )
    structured_source_count = len([line for line in sources.splitlines() if line.strip()])
    retrieved_context, retrieval_refs = build_context_and_refs(
        compressed,
        start_index=structured_source_count + 1,
    )
    allowed_doc_ids = {str(item["doc"].get("doc_id")) for item in compressed}
    grounding_evidence = f"{evidence}\n{retrieved_context}"
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.review",
            phase="review",
            status="completed",
            subquery_id=subquery_id,
            title="已审查环境证据边界",
            detail=f"确认 {len(allowed_doc_ids)} 条 RAG 证据和结构化证据可用于综合。",
        )
    reviewer_instructions = (
        DATA_ANALYSIS_SYSTEM_PROMPT
        + "\n可按‘结论、数据依据、配卡分析、数据边界’组织回答，小标题直接写中文，不添加井号或星号。"
    )
    evidence_label = "当前选定的 Supercell API 数据范围" if EXTERNAL_API_REQUIRED else "本地快照证据包"
    if parsed.get("intent") == "meta_analysis_query":
        reviewer_instructions = (
            DATA_ANALYSIS_SYSTEM_PROMPT
            + "\n只使用提供的证据分析环境。可使用‘结论、数据依据、数据边界’三个中文纯文本小标题，"
            "但小标题前不要加井号，不要使用星号强调。除非用户明确要求，否则不要加入训练建议、具体打法、"
            "对手侦察、三局两胜备战、赛程或推荐。不要输出参考来源标题或来源列表，运行时会追加已校验来源。"
        )
    strict_live_instruction = (
        "\n当前证据包来自选定的 Supercell API 对局范围：不得称其为本地静态文件或全局环境统计；"
        "RAG 文档只提供通用策略，不得把其中的卡组当作当前主流排行。"
        if EXTERNAL_API_REQUIRED
        else ""
    )
    reviewer_agent = ReActAgent(
        name="MetaEvidenceReviewer",
        sys_prompt=DATA_ANALYSIS_SYSTEM_PROMPT + strict_live_instruction,
        model=build_reviewer_model(api_key),
        formatter=OpenAIChatFormatter(),
        memory=InMemoryMemory(),
    )
    reviewer_agent.set_console_output_enabled(enabled=False)
    prompt = (
        f"用户问题：{user_text}\n"
        f"已解析意图：{parsed.get('intent')}\n\n"
        f"{evidence_label}：\n"
        f"{evidence}\n\n"
        "RAG 检索证据：\n"
        f"{retrieved_context}\n\n"
        "请严格按照系统规则回答。不要输出参考来源标题或来源列表；运行时会附加经过校验的来源。"
    )
    answer = ""
    model_streamed = False
    dropped_sentence_count = 0
    try:
        if uses_responses_api() and stream_content and event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.generate",
                phase="generate",
                status="running",
                subquery_id=subquery_id,
                title="正在生成环境结论",
                detail="模型正在基于检索证据输出公开文本。",
            )
            chunks: list[str] = []
            public_chunks: list[str] = []
            stream_buffer = GroundedStreamBuffer(
                grounding_evidence,
                allowed_doc_ids,
                stop_markers=("参考来源：", "参考来源:", "References:", "Sources:"),
                drop_unsupported=True,
            )
            try:
                async with asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS):
                    async for delta in generate_model_text_stream(
                        api_key=api_key,
                        instructions=reviewer_instructions,
                        input_text=prompt,
                        reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                    ):
                        chunks.append(delta)
                        for validated_chunk in stream_buffer.push(delta):
                            public_chunks.append(validated_chunk)
                            await event_sink.content(validated_chunk, delta=True)
                for validated_chunk in stream_buffer.finish():
                    public_chunks.append(validated_chunk)
                    await event_sink.content(validated_chunk, delta=True)
                answer = "".join(public_chunks).strip()
                if not answer:
                    answer = "模型生成的数值结论未通过证据校验，因此没有输出不可靠数据。"
                dropped_sentence_count = stream_buffer.dropped_count
                model_streamed = True
                if metadata is not None:
                    metadata["model_stream"] = "streaming"
            except Exception:
                if chunks:
                    raise
                if metadata is not None:
                    metadata["model_stream"] = "fallback_chunked"
                await event_sink.execution(
                    step_id=f"{subquery_id}.generate",
                    phase="generate",
                    status="running",
                    subquery_id=subquery_id,
                    title="模型未提供 token 流",
                    detail="正在使用同一模型的已完成结果分段输出。",
                )
                answer = await asyncio.wait_for(
                    generate_model_text(
                        api_key=api_key,
                        instructions=reviewer_instructions,
                        input_text=prompt,
                        reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                    ),
                    timeout=MODEL_CALL_TIMEOUT_SECONDS,
                )
        elif uses_responses_api():
            answer = await asyncio.wait_for(
                generate_model_text(
                    api_key=api_key,
                    instructions=reviewer_instructions,
                    input_text=prompt,
                    reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                ),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
            if metadata is not None:
                metadata["model_stream"] = "fallback_chunked"
        else:
            result = await asyncio.wait_for(
                reviewer_agent(Msg(name="user", role="user", content=prompt)),
                timeout=MODEL_CALL_TIMEOUT_SECONDS,
            )
            answer = extract_text_content(result).strip()
            if metadata is not None:
                metadata["model_stream"] = "fallback_chunked"
        if metadata is not None:
            metadata["model_generation"] = "api"
    except asyncio.TimeoutError:
        logger.warning("evidence synthesis model call timed out")
        if metadata is not None:
            metadata["model_generation"] = "retrieval_fallback_after_model_timeout"
            metadata["model_failure_type"] = "TimeoutError"
            metadata["model_stream"] = "fallback_chunked"
            metadata["degraded"] = True
            metadata["degraded_reason"] = "model_timeout"
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.degraded",
                phase="generate",
                status="completed",
                subquery_id=subquery_id,
                title="模型综合超时，已返回检索证据",
                detail="没有使用旧静态榜单，也没有生成证据之外的结论。",
            )
        answer = build_retrieved_evidence_fallback(compressed)
    except GroundingValidationError as exc:
        logger.warning("evidence grounding validation failed detail=%s", str(exc)[:1000])
        if metadata is not None:
            metadata["model_generation"] = "unavailable" if EXTERNAL_API_REQUIRED else "fallback_after_grounding_error"
            metadata["model_failure_type"] = type(exc).__name__
            metadata["model_stream"] = "unavailable"
            metadata["grounding_validation_error"] = str(exc)[:1000]
        if EXTERNAL_API_REQUIRED:
            raise RequiredExternalAPIError("RAG model API call failed: GroundingValidationError") from exc
        answer = build_snapshot_fallback_answer(top_decks_data, cards_meta_data)
    except Exception as exc:
        logger.exception("evidence synthesis model call failed")
        if metadata is not None:
            metadata["model_generation"] = "unavailable" if EXTERNAL_API_REQUIRED else "fallback_after_model_error"
            metadata["model_failure_type"] = type(exc).__name__
            metadata["model_stream"] = "unavailable"
        if EXTERNAL_API_REQUIRED:
            raise RequiredExternalAPIError(f"RAG model API call failed: {type(exc).__name__}") from exc
        answer = build_snapshot_fallback_answer(top_decks_data, cards_meta_data)
    answer = strip_generated_reference_section(answer)
    if not model_streamed:
        completed_buffer = GroundedStreamBuffer(
            grounding_evidence,
            allowed_doc_ids,
            drop_unsupported=True,
        )
        validated_parts = completed_buffer.push(answer)
        validated_parts += completed_buffer.finish()
        answer = "".join(validated_parts).strip()
        dropped_sentence_count = completed_buffer.dropped_count
        if not answer:
            answer = "模型生成的数值结论未通过证据校验，因此没有输出不可靠数据。"
    if dropped_sentence_count:
        boundary_notice = "\n\n数据边界：模型生成的部分数值句未通过证据校验，相关内容已省略。"
        answer += boundary_notice
        if metadata is not None:
            metadata["grounding_sentences_dropped"] = dropped_sentence_count
        if event_sink is not None and model_streamed:
            await event_sink.content(boundary_notice, delta=True)
    final_answer = f"{answer}\n\n参考来源：\n{sources}\n{retrieval_refs}"
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.validate",
            phase="validate",
            status="running",
            subquery_id=subquery_id,
            title="正在校验环境回答",
            detail="检查引用标识和数值事实是否受当前范围证据支持。",
        )
    try:
        grounding_validation = validate_answer_grounding(
            final_answer,
            grounding_evidence,
            allowed_doc_ids,
            raise_on_failure=RAG_FACT_VALIDATION_ENABLED and EXTERNAL_API_REQUIRED,
        )
    except GroundingValidationError as exc:
        logger.warning("final evidence grounding validation failed detail=%s", str(exc)[:1000])
        if metadata is not None:
            metadata["grounding_validation_error"] = str(exc)[:1000]
            metadata["grounding_fallback"] = "validated_refusal"
        answer = "模型生成的环境结论未通过最终证据校验，因此没有输出不可靠内容。"
        final_answer = f"{answer}\n\n参考来源：\n{sources}\n{retrieval_refs}"
        grounding_validation = validate_answer_grounding(
            final_answer,
            grounding_evidence,
            allowed_doc_ids,
            raise_on_failure=False,
        )
    if metadata is not None:
        metadata["grounding_validation"] = grounding_validation
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.validate",
            phase="validate",
            status="completed",
            subquery_id=subquery_id,
            title="环境回答校验通过",
            detail="回答通过当前数据范围的证据边界校验。",
        )
    if event_sink is not None and stream_content:
        if not model_streamed:
            chunks = [answer[start : start + 80] for start in range(0, len(answer), 80)]
            for index, chunk in enumerate(chunks):
                await event_sink.content(chunk, delta=True)
                if index < len(chunks) - 1:
                    await asyncio.sleep(FALLBACK_CONTENT_INTERVAL_SECONDS)
        await event_sink.content(f"\n\n参考来源：\n{sources}\n{retrieval_refs}", delta=True)
        await event_sink.execution(
            step_id=f"{subquery_id}.generate",
            phase="generate",
            status="completed",
            subquery_id=subquery_id,
            title="已生成环境结论",
            detail="回答仅基于当前检索证据和官方快照。",
        )
    return final_answer


DIRECT_SKILL_REGISTRY = build_default_registry(
    rag_answer_builder=build_rag_answer,
    reviewer_model_builder=build_reviewer_model,
    evidence_synthesis_builder=build_evidence_synthesis_answer,
)
SKILL_EXECUTOR = SkillExecutor(DIRECT_SKILL_REGISTRY)
RULE_BASED_PLANNER = RuleBasedPlanner()


def subquery_needs_rag(parsed: dict) -> bool:
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return True
    if intent == "deck_query":
        return (
            not parsed.get("deck_cards")
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    if intent == "card_query":
        return (
            parsed.get("entity_mode") != "loadout_entity"
            and
            parsed.get("card_name") is None
            and parsed.get("rank") is None
            and parsed.get("top_n") is None
        )
    return False


def subquery_title(parsed: dict) -> str:
    intent = parsed.get("intent")
    if intent == "card_query":
        return f"卡牌数据：{parsed.get('card_name') or '卡牌排行'}"
    if intent == "card_compare_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("compare_metric"), "表现")
        return f"{' 与 '.join(names[:2]) or '两张卡牌'} {metric}比较"
    if intent == "meta_analysis_query":
        return "环境分析：当前主流卡组"
    if intent == "match_preparation_query":
        return "已移除的战队备战功能"
    if intent == "card_cooccurrence_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        if len(names) >= 2:
            return f"{' 与 '.join(names[:2])} 共现统计"
        return f"{parsed.get('card_name') or '卡牌'} 常见搭配"
    if intent == "deck_query":
        if parsed.get("deck_cards"):
            return "精确八卡卡组统计"
        if parsed.get("card_name"):
            return f"{parsed['card_name']} 卡组"
        return "热门卡组"
    if intent == "schedule_query":
        return "已移除的战队赛程功能"
    return "子问题结果"


def subquery_user_text(parsed: dict, original_text: str) -> str:
    intent = parsed.get("intent")
    if intent == "meta_analysis_query":
        return original_text
    if intent == "match_preparation_query":
        return "已移除的战队备战功能"
    if (
        intent == "deck_query"
        and parsed.get("card_name") is None
        and parsed.get("rank") is None
        and parsed.get("top_n") is None
    ):
        return "当前热门卡组分析"
    if intent == "card_query" and parsed.get("entity_mode") == "loadout_entity":
        state = parsed.get("special_state") or "ordinary"
        return f"{state} {parsed.get('entity_name') or parsed.get('card_name') or ''} {' '.join(parsed.get('metrics') or [])}"
    if intent == "card_query" and parsed.get("card_name"):
        return f"{parsed['card_name']} {' '.join(parsed.get('metrics') or [])}"
    return original_text


def compose_multi_intent_answer(results: list[dict]) -> str:
    sections = []
    for result in results:
        answer = result.get("answer") or "当前子问题没有可用结果。"
        if result.get("status") == "failed":
            answer = f"无法完成：{result.get('error') or answer}"
        sections.append(f"## {result['title']}\n{answer}")
    return "\n\n".join(sections)


async def execute_subquery(
    *,
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
    trace_id: str,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository=None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = False,
) -> dict:
    subquery_id = str(parsed.get("id") or "q")
    started_at = time.perf_counter()
    context = SkillContext(
        user_text=subquery_user_text(parsed, user_text),
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        card_deck_stats=card_deck_stats or {},
        structured_repository=structured_repository,
        retriever=retriever,
        api_key=api_key,
        metadata={"trace_id": trace_id, "subquery_id": subquery_id, **(runtime_metadata or {})},
        event_sink=event_sink,
        stream_content=stream_content,
    )
    plan = RULE_BASED_PLANNER.build_plan(context)
    if plan is not None:
        context.metadata["plan"] = plan.to_dict()

    if event_sink is not None:
        candidate = DIRECT_SKILL_REGISTRY.resolve(parsed)
        await event_sink.execution(
            step_id=f"{subquery_id}.route",
            phase="route",
            status="running",
            subquery_id=subquery_id,
            title=f"正在执行{subquery_title(parsed)}",
            detail=f"路由到 {candidate.name if candidate else '未匹配 Skill'}。",
        )

    unavailable = None
    if subquery_needs_rag(parsed) and not api_key:
        unavailable = "OPENAI_API_KEY is not configured"
    elif subquery_needs_rag(parsed) and retriever is None:
        unavailable = "RAG retriever is unavailable"

    try:
        answer = await SKILL_EXECUTOR.execute(context)
        status = "unavailable" if unavailable else "success"
        if answer is None:
            status = "failed"
            answer = "没有匹配到可以安全处理该子问题的能力。"
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.route",
                phase="route",
                status="completed" if status == "success" else status,
                subquery_id=subquery_id,
                title=f"已完成{subquery_title(parsed)}",
                detail=f"使用 {context.metadata.get('selected_skill') or '未匹配 Skill'}。",
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
        return {
            "id": subquery_id,
            "title": subquery_title(parsed),
            "parsed": parsed,
            "plan": context.metadata.get("plan"),
            "selected_skill": context.metadata.get("selected_skill"),
            "mode": context.metadata.get("mode"),
            "status": status,
            "answer": answer,
            "metadata": dict(context.metadata),
            "error": unavailable,
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
        }
    except Exception as exc:
        logger.exception("subquery failed id=%s intent=%s", subquery_id, parsed.get("intent"))
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.route",
                phase="route",
                status="failed",
                subquery_id=subquery_id,
                title=f"{subquery_title(parsed)}未完成",
                detail=type(exc).__name__,
                elapsed_ms=int((time.perf_counter() - started_at) * 1000),
            )
        return {
            "id": subquery_id,
            "title": subquery_title(parsed),
            "parsed": parsed,
            "plan": context.metadata.get("plan"),
            "selected_skill": context.metadata.get("selected_skill"),
            "mode": context.metadata.get("mode"),
            "status": "failed",
            "answer": "",
            "metadata": dict(context.metadata),
            "error": str(exc),
            "latency_ms": int((time.perf_counter() - started_at) * 1000),
        }


async def answer_multi_intent_query(
    *,
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository=None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = False,
) -> AnswerResult:
    trace_id = SKILL_EXECUTOR.recorder.new_trace_id()
    started_at = time.perf_counter()
    subqueries = []
    seen_subqueries: set[tuple] = set()
    for item in parsed.get("subqueries", []):
        if not isinstance(item, dict):
            continue
        key = subquery_semantic_key(item)
        if key in seen_subqueries:
            continue
        seen_subqueries.add(key)
        subqueries.append(item)
    results = await asyncio.gather(
        *[
            execute_subquery(
                user_text=user_text,
                parsed=subquery,
                schedule_data=schedule_data,
                top_decks_data=top_decks_data,
                cards_meta_data=cards_meta_data,
                retriever=retriever,
                api_key=api_key,
                trace_id=trace_id,
                runtime_metadata=runtime_metadata,
                card_deck_stats=card_deck_stats,
                structured_repository=structured_repository,
                event_sink=event_sink,
                stream_content=stream_content,
            )
            for subquery in subqueries
        ]
    )
    plan = {
        "plan_type": "multi_intent",
        "subqueries": [
            {"id": result["id"], "intent": result["parsed"].get("intent"), "plan": result["plan"]}
            for result in results
        ],
    }
    metadata = {
        **(runtime_metadata or {}),
        "subquery_count": len(results),
        "sub_results": results,
        "total_latency_ms": int((time.perf_counter() - started_at) * 1000),
    }
    stream_modes = {
        str(result.get("metadata", {}).get("model_stream"))
        for result in results
        if isinstance(result.get("metadata"), dict)
    }
    metadata["model_stream"] = (
        "streaming"
        if "streaming" in stream_modes
        else "fallback_chunked"
        if "fallback_chunked" in stream_modes
        else "unavailable"
    )
    return AnswerResult(
        answer=compose_multi_intent_answer(results),
        trace_id=trace_id,
        parsed=parsed,
        plan=plan,
        selected_skill="MultiIntentOrchestrator",
        mode="mixed",
        metadata=metadata,
        sub_results=results,
    )


async def answer_query(
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: HybridRetriever | None,
    api_key: str,
    include_metadata: bool = False,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository=None,
    event_sink: RuntimeEventEmitter | None = None,
    stream_content: bool = True,
) -> str | AnswerResult:
    intent = parsed["intent"]
    if intent == "multi_intent":
        result = await answer_multi_intent_query(
            user_text=user_text,
            parsed=parsed,
            schedule_data=schedule_data,
            top_decks_data=top_decks_data,
            cards_meta_data=cards_meta_data,
            retriever=retriever,
            api_key=api_key,
            runtime_metadata=runtime_metadata,
            card_deck_stats=card_deck_stats,
            structured_repository=structured_repository,
            event_sink=event_sink,
            stream_content=stream_content,
        )
        return result if include_metadata else result.answer
    context = SkillContext(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        card_deck_stats=card_deck_stats or {},
        structured_repository=structured_repository,
        retriever=retriever,
        api_key=api_key,
        metadata=dict(runtime_metadata or {}),
        event_sink=event_sink,
        stream_content=stream_content,
    )
    plan = RULE_BASED_PLANNER.build_plan(context)
    if plan is not None:
        context.metadata["plan"] = plan.to_dict()
    answer = await SKILL_EXECUTOR.execute(context)
    if answer is not None:
        logger.info("answer route intent=%s mode=skill_executor", intent)
        if include_metadata:
            return AnswerResult(
                answer=answer,
                trace_id=context.metadata.get("trace_id"),
                parsed=parsed,
                plan=context.metadata.get("plan"),
                selected_skill=context.metadata.get("selected_skill"),
                mode=context.metadata.get("mode"),
                metadata=dict(context.metadata),
            )
        return answer

    logger.info("answer route intent=reject mode=fallback")
    fallback_answer = (
        "当前系统主要支持：\n"
        "- 单卡使用率/胜率查询\n"
        "- 卡牌比较与排行榜查询\n"
        "- 热门卡组查询\n"
        "- 基于证据的环境分析"
    )
    if include_metadata:
        return AnswerResult(
            answer=fallback_answer,
            trace_id=context.metadata.get("trace_id"),
            parsed=parsed,
            plan=context.metadata.get("plan"),
            selected_skill=None,
            mode="fallback",
            metadata=dict(context.metadata),
        )
    return fallback_answer


def read_trace(trace_id: str | None) -> list[dict]:
    if not trace_id:
        return []
    return SKILL_EXECUTOR.recorder.read_trace(trace_id)
