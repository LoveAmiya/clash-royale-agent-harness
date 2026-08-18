from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg

from answer_builder import build_retrieval_query as default_build_retrieval_query
from app_config import (
    COMPRESS_CHAR_BUDGET,
    COMPRESS_MAX_ITEMS,
    RERANK_TOP_N,
    RETRIEVAL_ALPHA,
    RETRIEVAL_FINAL_TOP_K,
    RETRIEVAL_TOP_K_BM25,
    RETRIEVAL_TOP_K_DENSE,
    SYNTHESIS_REASONING_EFFORT,
    MODEL_CALL_TIMEOUT_SECONDS,
    EXTERNAL_API_REQUIRED,
    RAG_FACT_VALIDATION_ENABLED,
)
from clashroyale_agent.qa.evidence_grounding import (
    GroundingValidationError,
    append_references_if_missing as default_append_references_if_missing,
    build_evidence_ledger as default_build_evidence_ledger,
    build_reference_suffix as default_build_reference_suffix,
    create_grounded_stream_buffer as default_create_grounded_stream_buffer,
    validate_ledger_grounding as default_validate_ledger_grounding,
)
from clashroyale_agent.qa.presentation import emit_chunked_content as default_emit_chunked_content
from clashroyale_agent.qa.retrieval_orchestration import summarize_retrieval as default_summarize_retrieval
from model_gateway import (
    generate_model_text as default_generate_model_text,
    generate_model_text_stream as default_generate_model_text_stream,
    uses_responses_api as default_uses_responses_api,
)
from query_parser import extract_text_content as default_extract_text_content
from retrieval_postprocess import (
    compress_results as default_compress_results,
    rerank_results as default_rerank_results,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RagAnswerDependencies:
    append_references_if_missing: Any = default_append_references_if_missing
    build_evidence_ledger: Any = default_build_evidence_ledger
    build_reference_suffix: Any = default_build_reference_suffix
    build_retrieval_query: Any = default_build_retrieval_query
    compress_results: Any = default_compress_results
    create_grounded_stream_buffer: Any = default_create_grounded_stream_buffer
    emit_chunked_content: Any = default_emit_chunked_content
    extract_text_content: Any = default_extract_text_content
    generate_model_text: Any = default_generate_model_text
    generate_model_text_stream: Any = default_generate_model_text_stream
    logger: Any = logger
    rerank_results: Any = default_rerank_results
    summarize_retrieval: Any = default_summarize_retrieval
    uses_responses_api: Any = default_uses_responses_api
    validate_ledger_grounding: Any = default_validate_ledger_grounding
    compress_char_budget: int = COMPRESS_CHAR_BUDGET
    compress_max_items: int = COMPRESS_MAX_ITEMS
    external_api_required: bool = EXTERNAL_API_REQUIRED
    model_call_timeout_seconds: float = MODEL_CALL_TIMEOUT_SECONDS
    rag_fact_validation_enabled: bool = RAG_FACT_VALIDATION_ENABLED
    rerank_top_n: int = RERANK_TOP_N
    retrieval_alpha: float = RETRIEVAL_ALPHA
    retrieval_final_top_k: int = RETRIEVAL_FINAL_TOP_K
    retrieval_top_k_bm25: int = RETRIEVAL_TOP_K_BM25
    retrieval_top_k_dense: int = RETRIEVAL_TOP_K_DENSE
    synthesis_reasoning_effort: str = SYNTHESIS_REASONING_EFFORT


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
    dependencies: RagAnswerDependencies | None = None,
) -> str:
    deps = dependencies or RagAnswerDependencies()
    append_references_if_missing = deps.append_references_if_missing
    build_evidence_ledger = deps.build_evidence_ledger
    build_reference_suffix = deps.build_reference_suffix
    build_retrieval_query = deps.build_retrieval_query
    compress_results = deps.compress_results
    create_grounded_stream_buffer = deps.create_grounded_stream_buffer
    emit_chunked_content = deps.emit_chunked_content
    extract_text_content = deps.extract_text_content
    generate_model_text = deps.generate_model_text
    generate_model_text_stream = deps.generate_model_text_stream
    logger = deps.logger
    rerank_results = deps.rerank_results
    summarize_retrieval = deps.summarize_retrieval
    uses_responses_api = deps.uses_responses_api
    validate_ledger_grounding = deps.validate_ledger_grounding
    COMPRESS_CHAR_BUDGET = deps.compress_char_budget
    COMPRESS_MAX_ITEMS = deps.compress_max_items
    EXTERNAL_API_REQUIRED = deps.external_api_required
    MODEL_CALL_TIMEOUT_SECONDS = deps.model_call_timeout_seconds
    RAG_FACT_VALIDATION_ENABLED = deps.rag_fact_validation_enabled
    RERANK_TOP_N = deps.rerank_top_n
    RETRIEVAL_ALPHA = deps.retrieval_alpha
    RETRIEVAL_FINAL_TOP_K = deps.retrieval_final_top_k
    RETRIEVAL_TOP_K_BM25 = deps.retrieval_top_k_bm25
    RETRIEVAL_TOP_K_DENSE = deps.retrieval_top_k_dense
    SYNTHESIS_REASONING_EFFORT = deps.synthesis_reasoning_effort
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

    retrieval_started = time.perf_counter()
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
    retrieval_latency_ms = int((time.perf_counter() - retrieval_started) * 1000)
    retrieval_summary = summarize_retrieval(results)
    retrieval_summary["retrieval_latency_ms"] = retrieval_latency_ms
    if metadata is not None:
        metadata["retrieval"] = retrieval_summary
        metadata["retrieval_mode"] = retrieval_summary["retrieval_mode"]
        metadata["retrieved_doc_ids"] = [item["doc"].get("doc_id") for item in results]
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.retrieve",
            phase="retrieve",
            status="completed",
            subquery_id=subquery_id,
            title="已检索 RAG 证据",
            detail=(
                f"找到 {len(results)} 条候选证据；"
                f"融合={retrieval_summary['fusion_mode']}，"
                f"候选池={retrieval_summary['lane_candidate_pools'].get('single', {})}；"
                f"耗时={retrieval_latency_ms}ms。"
            ),
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
    rerank_started = time.perf_counter()
    reranked = rerank_results(
        question=user_text,
        parsed=parsed,
        results=results,
        top_n=RERANK_TOP_N,
    )
    rerank_latency_ms = int((time.perf_counter() - rerank_started) * 1000)
    if metadata is not None:
        metadata["retrieval"]["rerank_latency_ms"] = rerank_latency_ms
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="completed",
            subquery_id=subquery_id,
            title="已重排序证据",
            detail=f"保留 {len(reranked)} 条高相关候选证据；耗时={rerank_latency_ms}ms。",
        )

    if not reranked:
        return "我不知道，当前数据里没有足够高相关的候选结果。\n参考来源：无"

    compressed = compress_results(
        reranked,
        max_items=COMPRESS_MAX_ITEMS,
        char_budget=COMPRESS_CHAR_BUDGET,
    )
    if metadata is not None:
        metadata["retrieval"]["reranked_count"] = len(reranked)
        metadata["retrieval"]["evidence_count"] = len(compressed)
        metadata["retrieval"]["evidence_char_budget"] = COMPRESS_CHAR_BUDGET

    if not compressed:
        return "我不知道，当前数据里没有足够可用的压缩上下文。\n参考来源：无"

    evidence_ledger = build_evidence_ledger(compressed)
    retrieved_context = evidence_ledger.context
    refs_text = evidence_ledger.references
    allowed_doc_ids = evidence_ledger.allowed_doc_ids
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
            stream_buffer = create_grounded_stream_buffer(evidence_ledger)
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
                    reference_suffix = build_reference_suffix(evidence_ledger)
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
                validation = validate_ledger_grounding(
                    answer,
                    evidence_ledger,
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
        answer = append_references_if_missing(answer, evidence_ledger)
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.validate",
                phase="validate",
                status="running",
                subquery_id=subquery_id,
                title="正在校验回答",
                detail="检查引用标识和数值事实是否受证据支持。",
            )
        validation = validate_ledger_grounding(
            answer,
            evidence_ledger,
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
            await emit_chunked_content(event_sink, answer)
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


__all__ = ["RagAnswerDependencies", "build_rag_answer"]
