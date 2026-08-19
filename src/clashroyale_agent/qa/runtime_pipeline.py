from __future__ import annotations

from typing import Any, Mapping

from clashroyale_agent.qa.runtime_answering import (
    AnswerExecutionContext,
    AnswerPipelineDependencies,
    prepare_answer_execution_context,
    run_answer_pipeline,
)
from clashroyale_agent.qa.runtime_parsing import (
    AnswerParseDependencies,
    ParsedAnswerRequest,
    parse_answer_request,
)


async def run_runtime_answer_pipeline(
    *,
    runtime: Mapping[str, Any],
    user_text: str,
    app: Any,
    event_sink: Any,
    request_id: str | None,
    intent_hint: str | None,
    dataset_scope: str,
    deck_mode: str,
    entity_mode: str,
) -> Any:
    dataset_scope = runtime["_validate_dataset_scope"](dataset_scope)
    bootstrap_cards_meta_data = getattr(app.state, "bootstrap_cards_meta_data", app.state.cards_meta_data)
    cards_meta_data = app.state.cards_meta_data
    schedule_data = app.state.schedule_data
    top_decks_data = app.state.top_decks_data
    card_deck_stats_data = getattr(app.state, "card_deck_stats_data", {})
    api_key = runtime["os"].getenv("OPENAI_API_KEY")

    parse_dependencies = AnswerParseDependencies(
        detect_unsupported_analysis_request=runtime["detect_unsupported_analysis_request"],
        build_analysis_boundary_answer=runtime["build_analysis_boundary_answer"],
        build_external_api_unavailable_result=runtime["build_external_api_unavailable_result"],
        parse_user_query=runtime["parse_user_query"],
        apply_selected_entity_mode=runtime["apply_selected_entity_mode"],
        describe_parsed_request=runtime["describe_parsed_request"],
        logger=runtime["logger"],
        high_confidence=runtime["LOCAL_PARSE_CONFIDENCE_HIGH"],
        medium_confidence=runtime["LOCAL_PARSE_CONFIDENCE_MEDIUM"],
        model_name=runtime["OPENAI_MODEL"],
    )
    parsed_request = await parse_answer_request(
        dependencies=parse_dependencies,
        user_text=user_text,
        bootstrap_cards_meta_data=bootstrap_cards_meta_data,
        api_key=api_key,
        external_api_required=runtime["EXTERNAL_API_REQUIRED"],
        intent_hint=intent_hint,
        deck_mode=deck_mode,
        entity_mode=entity_mode,
        request_id=request_id,
        event_sink=event_sink,
    )
    if not isinstance(parsed_request, ParsedAnswerRequest):
        return parsed_request

    pipeline_dependencies = AnswerPipelineDependencies(
        select_answer_data_context=runtime["select_answer_data_context"],
        query_needs_rag=runtime["query_needs_rag"],
        query_requires_official_snapshot=runtime["query_requires_official_snapshot"],
        get_structured_repository=runtime["get_structured_repository"],
        active_snapshot_group_manifest=runtime["_active_snapshot_group_manifest"],
        merge_live_card_snapshot=runtime["merge_live_card_snapshot"],
        snapshot_refresh_due=runtime["snapshot_refresh_due"],
        build_external_api_unavailable_result=runtime["build_external_api_unavailable_result"],
        ensure_dataset_retriever=runtime["ensure_dataset_retriever"],
        execute_grounded_answer=runtime["execute_grounded_answer"],
        answer_query=runtime["answer_query"],
        normalize_answer_text=runtime["normalize_answer_text"],
        emit_semantic_content=runtime["emit_semantic_content"],
    )
    execution_context = await prepare_answer_execution_context(
        dependencies=pipeline_dependencies,
        parsed=parsed_request.parsed,
        app=app,
        dataset_scope=dataset_scope,
        cards_meta_data=cards_meta_data,
        top_decks_data=top_decks_data,
        card_deck_stats_data=card_deck_stats_data,
        external_api_required=runtime["EXTERNAL_API_REQUIRED"],
        supercell_live_data_enabled=runtime["SUPERCELL_LIVE_DATA_ENABLED"],
        supercell_api_token=runtime["SUPERCELL_API_TOKEN"],
        data_dir=runtime["DATA_DIR"],
        event_sink=event_sink,
    )
    if not isinstance(execution_context, AnswerExecutionContext):
        return execution_context

    if event_sink is not None:
        await event_sink.execution(
            step_id="route",
            phase="route",
            status="completed",
            title="已确定执行路径",
            detail=(
                "多意图子任务将并发执行并按提问顺序汇总。"
                if parsed_request.parsed.get("intent") == "multi_intent"
                else "将执行 RAG 证据分析。"
                if runtime["query_needs_rag"](parsed_request.parsed)
                else "将执行已验证的结构化查询，不调用 RAG。"
            ),
        )
    return await run_answer_pipeline(
        dependencies=pipeline_dependencies,
        user_text=user_text,
        parsed=parsed_request.parsed,
        schedule_data=schedule_data,
        execution_context=execution_context,
        api_key=api_key or "",
        request_id=request_id,
        dataset_scope=dataset_scope,
        deck_mode=parsed_request.deck_mode,
        entity_mode=parsed_request.entity_mode,
        parser_api=parsed_request.parser_api,
        event_sink=event_sink,
    )


__all__ = ["run_runtime_answer_pipeline"]
