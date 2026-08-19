from __future__ import annotations

import asyncio
import time
from typing import Any

from clashroyale_agent.qa.evidence_grounding import (
    GroundingValidationError,
)
from clashroyale_agent.qa.streaming import ModelFirstTokenTimeout, ModelStreamStartupError
from clashroyale_agent.qa.synthesis_contracts import RequiredExternalAPIError
from clashroyale_agent.qa.synthesis_dependencies import EvidenceSynthesisDependencies


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
    dependencies: EvidenceSynthesisDependencies | None = None,
) -> str:
    """通过 RAG 检索和静态快照完成可追溯的开放问题综合。"""
    deps = dependencies or EvidenceSynthesisDependencies()
    build_evidence_ledger = deps.build_evidence_ledger
    build_meta_evidence_pack = deps.build_meta_evidence_pack
    build_retrieved_evidence_fallback = deps.build_retrieved_evidence_fallback
    build_reviewer_model = deps.build_reviewer_model
    build_snapshot_fallback_answer = deps.build_snapshot_fallback_answer
    compress_results = deps.compress_results
    create_grounded_stream_buffer = deps.create_grounded_stream_buffer
    emit_chunked_content = deps.emit_chunked_content
    extract_text_content = deps.extract_text_content
    filter_completed_answer = deps.filter_completed_answer
    generate_model_text = deps.generate_model_text
    generate_model_text_stream = deps.generate_model_text_stream
    logger = deps.logger
    rerank_results = deps.rerank_results
    retrieve_meta_candidates = deps.retrieve_meta_candidates
    select_diverse_results = deps.select_diverse_results
    stream_with_first_token_watchdog = deps.stream_with_first_token_watchdog
    summarize_retrieval = deps.summarize_retrieval
    uses_responses_api = deps.uses_responses_api
    validate_ledger_grounding = deps.validate_ledger_grounding
    ReActAgent = deps.re_act_agent_cls
    OpenAIChatFormatter = deps.openai_chat_formatter_cls
    InMemoryMemory = deps.in_memory_memory_cls
    Msg = deps.msg_cls
    RequiredExternalAPIError = deps.required_external_api_error_cls
    DATA_ANALYSIS_SYSTEM_PROMPT = deps.data_analysis_system_prompt
    EXTERNAL_API_REQUIRED = deps.external_api_required
    META_COMPRESS_CHAR_BUDGET = deps.meta_compress_char_budget
    META_COMPRESS_MAX_ITEMS = deps.meta_compress_max_items
    META_EVIDENCE_LANES = deps.meta_evidence_lanes
    META_RERANK_TOP_N = deps.meta_rerank_top_n
    META_RETRIEVAL_LANE_TOP_K = deps.meta_retrieval_lane_top_k
    MODEL_CALL_TIMEOUT_SECONDS = deps.model_call_timeout_seconds
    OPENAI_REVIEW_MODEL = deps.openai_review_model
    RAG_FACT_VALIDATION_ENABLED = deps.rag_fact_validation_enabled
    RETRIEVAL_FUSION_MODE = deps.retrieval_fusion_mode
    RETRIEVAL_TOP_K_BM25 = deps.retrieval_top_k_bm25
    RETRIEVAL_TOP_K_DENSE = deps.retrieval_top_k_dense
    SYNTHESIS_REASONING_EFFORT = deps.synthesis_reasoning_effort

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
            operation="retrieval.hybrid_search",
            parameters={
                "scope": (metadata or {}).get("dataset_scope"),
                "deck_mode": (metadata or {}).get("deck_mode"),
                "entity_mode": (metadata or {}).get("entity_mode"),
                "bm25_top_k": RETRIEVAL_TOP_K_BM25,
                "dense_top_k": RETRIEVAL_TOP_K_DENSE,
                "fusion": RETRIEVAL_FUSION_MODE,
                "final_top_k": META_RETRIEVAL_LANE_TOP_K,
                "lanes": list(META_EVIDENCE_LANES),
            },
            rationale="用户询问当前环境，需要从选定数据范围召回覆盖多个证据类型的候选。",
            boundaries=["检索阶段只寻找候选证据，不在此阶段生成环境结论。"],
        )
    retrieval_started = time.perf_counter()
    results, retrieval_lanes = retrieve_meta_candidates(
        retriever,
        user_text,
        dataset_scope=(metadata or {}).get("dataset_scope"),
        deck_mode=(metadata or {}).get("deck_mode"),
        entity_mode=(metadata or {}).get("entity_mode"),
        source_type=retrieval_source_type,
    )
    retrieval_latency_ms = int((time.perf_counter() - retrieval_started) * 1000)
    if parsed.get("analysis_type") == "meta_delta":
        selected_scope = str((metadata or {}).get("dataset_scope") or "")
        results = [
            item
            for item in results
            if str(item.get("doc", {}).get("metadata", {}).get("baseline_scope") or "")
            and str(item.get("doc", {}).get("metadata", {}).get("baseline_scope")) != selected_scope
        ]
    retrieval_summary = summarize_retrieval(results, lanes=retrieval_lanes)
    retrieval_summary["retrieval_latency_ms"] = retrieval_latency_ms
    if metadata is not None:
        metadata["retrieval_mode"] = results[0].get("retrieval_mode", "none") if results else "none"
        metadata["retrieved_doc_ids"] = [item["doc"].get("doc_id") for item in results]
        metadata["retrieval_source_type"] = retrieval_source_type
        metadata["retrieval_lanes"] = retrieval_lanes
        metadata["retrieval"] = retrieval_summary
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
            detail=(
                f"找到 {len(results)} 条候选证据；"
                f"融合={retrieval_summary['fusion_mode']}，"
                f"召回通道={len(retrieval_lanes)}；耗时={retrieval_latency_ms}ms。"
            ),
            operation="retrieval.hybrid_search",
            parameters={
                "scope": (metadata or {}).get("dataset_scope"),
                "candidate_count": len(results),
                "fusion": retrieval_summary["fusion_mode"],
                "lanes": len(retrieval_lanes),
            },
            evidence=[f"当前范围共召回 {len(results)} 条候选证据。"],
            boundaries=["候选数量不等于最终引用数量，仍需精排和多样性筛选。"],
        )
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="running",
            subquery_id=subquery_id,
            title="正在重排序环境证据",
            detail="按问题相关性对环境候选证据进行确定性重排序。",
            operation="rerank.select_diverse",
            parameters={"rerank_top_n": META_RERANK_TOP_N},
            rationale="优先保留与问题直接相关且来源分布不过度集中的证据。",
        )
    rerank_started = time.perf_counter()
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
    rerank_latency_ms = int((time.perf_counter() - rerank_started) * 1000)
    if metadata is not None:
        metadata["retrieval"]["rerank_latency_ms"] = rerank_latency_ms
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.rerank",
            phase="rerank",
            status="completed",
            subquery_id=subquery_id,
            title="已重排序环境证据",
            detail=f"保留 {len(reranked)} 条高相关候选证据；耗时={rerank_latency_ms}ms。",
            operation="rerank.select_diverse",
            parameters={"rerank_top_n": META_RERANK_TOP_N, "evidence_count": len(reranked)},
            evidence=[f"精排后保留 {len(reranked)} 条证据，每类来源最多 3 条。"],
            boundaries=["重排分数表示与问题的相关性，不表示卡牌或卡组强度。"],
        )
    compressed = compress_results(
        reranked,
        max_items=META_COMPRESS_MAX_ITEMS,
        char_budget=META_COMPRESS_CHAR_BUDGET,
    )
    if metadata is not None:
        metadata["retrieval"]["reranked_count"] = len(reranked)
        metadata["retrieval"]["evidence_count"] = len(compressed)
        metadata["retrieval"]["evidence_char_budget"] = META_COMPRESS_CHAR_BUDGET
    if not compressed:
        return (
            "当前知识库没有检索到足够相关的证据，因此不生成策略性结论。"
            "请补充对应的卡组、对手或战术资料后重试。\n\n参考来源：\n"
            f"{sources}"
        )
    structured_source_count = len([line for line in sources.splitlines() if line.strip()])
    evidence_ledger = build_evidence_ledger(
        compressed,
        start_index=structured_source_count + 1,
        structured_evidence=evidence,
    )
    retrieved_context = evidence_ledger.context
    retrieval_refs = evidence_ledger.references
    allowed_doc_ids = evidence_ledger.allowed_doc_ids
    grounding_evidence = evidence_ledger.grounding_evidence
    if event_sink is not None:
        await event_sink.execution(
            step_id=f"{subquery_id}.review",
            phase="review",
            status="completed",
            subquery_id=subquery_id,
            title="已审查环境证据边界",
            detail=f"确认 {len(allowed_doc_ids)} 条 RAG 证据和结构化证据可用于综合。",
            operation="evidence.review_boundaries",
            parameters={"evidence_count": len(allowed_doc_ids)},
            rationale="只允许模型使用本次选定范围内、通过压缩与引用登记的证据。",
            evidence=[f"{len(allowed_doc_ids)} 条 RAG 证据进入生成上下文。"],
            boundaries=[
                "使用率表示本样本中的出现频率，不能单独证明强度或因果。",
                "胜率需要结合样本量、数据范围和对局选择偏差解释。",
            ],
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
                operation="synthesize.evidence_grounded",
                parameters={
                    "mode": "evidence_grounded",
                    "effort": SYNTHESIS_REASONING_EFFORT,
                    "stream": True,
                    "timeout_seconds": MODEL_CALL_TIMEOUT_SECONDS,
                    "model": OPENAI_REVIEW_MODEL,
                },
                rationale="把已校验的结构化指标和 RAG 证据组织成可读结论，同时保留数据边界。",
                boundaries=["不展示内部提示词或私有思维链，只展示可审计的执行依据。"],
            )
            chunks: list[str] = []
            public_chunks: list[str] = []
            stream_buffer = create_grounded_stream_buffer(
                evidence_ledger,
                stop_markers=("参考来源：", "参考来源:", "References:", "Sources:"),
                drop_unsupported=True,
            )
            try:
                async with asyncio.timeout(MODEL_CALL_TIMEOUT_SECONDS):
                    model_stream = generate_model_text_stream(
                        api_key=api_key,
                        instructions=reviewer_instructions,
                        input_text=prompt,
                        reasoning_effort=SYNTHESIS_REASONING_EFFORT,
                    )
                    async for delta in stream_with_first_token_watchdog(
                        model_stream,
                        event_sink=event_sink,
                        step_id=f"{subquery_id}.generate",
                        subquery_id=subquery_id,
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
            except ModelFirstTokenTimeout:
                raise
            except Exception as exc:
                if chunks:
                    raise
                # Do not start a second full-length model call here. The old
                # retry could turn one 120-second bound into roughly 240 seconds.
                raise ModelStreamStartupError("model stream failed before first public text") from exc
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
    except asyncio.TimeoutError as exc:
        logger.warning("evidence synthesis model call timed out")
        first_token_timeout = isinstance(exc, ModelFirstTokenTimeout)
        if metadata is not None:
            metadata["model_generation"] = "retrieval_fallback_after_model_timeout"
            metadata["model_failure_type"] = type(exc).__name__
            metadata["model_stream"] = "fallback_chunked"
            metadata["degraded"] = True
            metadata["degraded_reason"] = "model_first_token_timeout" if first_token_timeout else "model_timeout"
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.degraded",
                phase="generate",
                status="completed",
                subquery_id=subquery_id,
                title="模型首段文本等待超时，已返回检索证据" if first_token_timeout else "模型综合超时，已返回检索证据",
                detail="没有发起第二次长模型调用；返回内容只来自本轮已经校验的检索证据。",
            )
        answer = build_retrieved_evidence_fallback(compressed)
    except ModelStreamStartupError as exc:
        cause = exc.__cause__ or exc
        logger.warning("evidence synthesis stream startup failed type=%s", type(cause).__name__)
        if metadata is not None:
            metadata["model_generation"] = "retrieval_fallback_after_stream_error"
            metadata["model_failure_type"] = type(cause).__name__
            metadata["model_stream"] = "fallback_chunked"
            metadata["degraded"] = True
            metadata["degraded_reason"] = "model_stream_startup_error"
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.degraded",
                phase="generate",
                status="completed",
                subquery_id=subquery_id,
                title="模型流未启动，已返回检索证据",
                detail="没有发起第二次长模型调用；返回内容只来自本轮已经校验的检索证据。",
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
    if not model_streamed:
        grounded_answer = filter_completed_answer(answer, evidence_ledger)
        answer = grounded_answer.answer
        dropped_sentence_count = grounded_answer.dropped_sentence_count
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
            operation="grounding.validate",
            parameters={"evidence_count": len(allowed_doc_ids)},
            rationale="最终答案中的数值和引用必须能回指到本轮允许的证据。",
        )
    try:
        grounding_validation = validate_ledger_grounding(
            final_answer,
            evidence_ledger,
            raise_on_failure=RAG_FACT_VALIDATION_ENABLED and EXTERNAL_API_REQUIRED,
        )
    except GroundingValidationError as exc:
        logger.warning("final evidence grounding validation failed detail=%s", str(exc)[:1000])
        if metadata is not None:
            metadata["grounding_validation_error"] = str(exc)[:1000]
            metadata["grounding_fallback"] = "validated_refusal"
        answer = "模型生成的环境结论未通过最终证据校验，因此没有输出不可靠内容。"
        final_answer = f"{answer}\n\n参考来源：\n{sources}\n{retrieval_refs}"
        grounding_validation = validate_ledger_grounding(
            final_answer,
            evidence_ledger,
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
            operation="grounding.validate",
            parameters={"evidence_count": len(allowed_doc_ids)},
            evidence=["数值事实与引用标识已完成证据边界校验。"],
            boundaries=["结论只适用于界面当前选择的数据范围和快照。"],
        )
    if event_sink is not None and stream_content:
        if not model_streamed:
            await emit_chunked_content(event_sink, answer)
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


__all__ = [
    "EvidenceSynthesisDependencies",
    "RequiredExternalAPIError",
    "build_evidence_synthesis_answer",
]
