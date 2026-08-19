from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from query_answering import AnswerResult

from clashroyale_agent.qa.runtime_data_context import AnswerDataContext


@dataclass(frozen=True)
class AnswerPipelineDependencies:
    """Runtime-provided services for the answer pipeline compatibility facade."""

    select_answer_data_context: Callable[..., Any]
    query_needs_rag: Callable[[dict], bool]
    query_requires_official_snapshot: Callable[[dict], bool]
    get_structured_repository: Callable[[Any, str], Any]
    active_snapshot_group_manifest: Callable[[Any], dict | None]
    merge_live_card_snapshot: Callable[[list[dict], list[dict]], list[dict]]
    snapshot_refresh_due: Callable[[dict], bool]
    build_external_api_unavailable_result: Callable[[dict, str, dict], AnswerResult]
    ensure_dataset_retriever: Callable[[Any, str], Any]
    execute_grounded_answer: Callable[..., Any]
    answer_query: Callable[..., Any]
    normalize_answer_text: Callable[[str], str]
    emit_semantic_content: Callable[..., Any]


@dataclass
class AnswerExecutionContext:
    cards_meta_data: list[dict]
    top_decks_data: list[dict]
    card_deck_stats_data: dict
    structured_repository: Any
    retriever: Any
    data_context: dict
    live_metadata: dict
    rag_metadata: dict


async def prepare_answer_execution_context(
    *,
    dependencies: AnswerPipelineDependencies,
    parsed: dict,
    app: Any,
    dataset_scope: str,
    cards_meta_data: list[dict],
    top_decks_data: list[dict],
    card_deck_stats_data: dict,
    external_api_required: bool,
    supercell_live_data_enabled: bool,
    supercell_api_token: str | None,
    data_dir: Any,
    event_sink: Any = None,
) -> AnswerExecutionContext | AnswerResult:
    data_selection = await dependencies.select_answer_data_context(
        parsed=parsed,
        app=app,
        dataset_scope=dataset_scope,
        cards_meta_data=cards_meta_data,
        top_decks_data=top_decks_data,
        card_deck_stats_data=card_deck_stats_data,
        external_api_required=external_api_required,
        supercell_live_data_enabled=supercell_live_data_enabled,
        supercell_api_token=supercell_api_token,
        data_dir=data_dir,
        query_needs_rag=dependencies.query_needs_rag,
        query_requires_official_snapshot=dependencies.query_requires_official_snapshot,
        get_structured_repository=dependencies.get_structured_repository,
        active_snapshot_group_manifest=dependencies.active_snapshot_group_manifest,
        merge_live_card_snapshot=dependencies.merge_live_card_snapshot,
        snapshot_refresh_due=dependencies.snapshot_refresh_due,
        build_external_api_unavailable_result=dependencies.build_external_api_unavailable_result,
        event_sink=event_sink,
    )
    if not isinstance(data_selection, AnswerDataContext):
        return data_selection

    retriever = (
        dependencies.ensure_dataset_retriever(app, dataset_scope)
        if dependencies.query_needs_rag(parsed)
        else None
    )
    if data_selection.rolling_manifest is not None:
        rag_metadata = {
            "status": "ready" if retriever is not None else "not_ready",
            "snapshot_group_id": data_selection.rolling_manifest["snapshot_group_id"],
            "snapshot_id": data_selection.rolling_manifest["datasets"][dataset_scope]["snapshot_id"],
            "dataset_scope": dataset_scope,
            "docs_fingerprint": data_selection.rolling_manifest.get("rag_docs_fingerprint"),
        }
    else:
        rag_metadata = {
            "status": getattr(app.state, "rag_status", "not_required"),
            "snapshot_id": getattr(app.state, "rag_snapshot_id", None),
            "dataset_scope": dataset_scope,
            "docs_fingerprint": getattr(app.state, "rag_docs_fingerprint", None),
        }
    if dependencies.query_needs_rag(parsed):
        # Request handling only reads the already-preheated index.
        retriever = dependencies.ensure_dataset_retriever(app, dataset_scope)

    return AnswerExecutionContext(
        cards_meta_data=data_selection.cards_meta_data,
        top_decks_data=data_selection.top_decks_data,
        card_deck_stats_data=data_selection.card_deck_stats_data,
        structured_repository=data_selection.structured_repository,
        retriever=retriever,
        data_context=data_selection.data_context,
        live_metadata=data_selection.live_metadata,
        rag_metadata=rag_metadata,
    )


async def run_answer_pipeline(
    *,
    dependencies: AnswerPipelineDependencies,
    user_text: str,
    parsed: dict,
    schedule_data: Any,
    execution_context: AnswerExecutionContext,
    api_key: str,
    request_id: str | None,
    dataset_scope: str,
    deck_mode: str,
    entity_mode: str,
    parser_api: dict,
    event_sink: Any,
) -> AnswerResult:
    result = await dependencies.execute_grounded_answer(
        answer_query=dependencies.answer_query,
        normalize_answer_text=dependencies.normalize_answer_text,
        emit_semantic_content=dependencies.emit_semantic_content,
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=execution_context.top_decks_data,
        cards_meta_data=execution_context.cards_meta_data,
        card_deck_stats=execution_context.card_deck_stats_data,
        structured_repository=execution_context.structured_repository,
        retriever=execution_context.retriever,
        api_key=api_key,
        runtime_metadata={
            "request_id": request_id,
            "rag_status": execution_context.rag_metadata["status"],
            "rag_snapshot_id": execution_context.rag_metadata["snapshot_id"],
            "dataset_scope": dataset_scope,
            "deck_mode": deck_mode,
            "entity_mode": entity_mode,
            "data_context": execution_context.data_context,
        },
        event_sink=event_sink,
        stream_content=parsed.get("intent") != "multi_intent" and dependencies.query_needs_rag(parsed),
    )
    result.metadata["live_data"] = execution_context.live_metadata
    result.metadata["parser_api"] = parser_api
    result.metadata["rag"] = execution_context.rag_metadata
    result.metadata["data_context"] = execution_context.data_context
    result.metadata["presentation"] = "plain_text_zh_cn_v1"
    if request_id:
        result.metadata["request_id"] = request_id
    return result


async def execute_grounded_answer(
    *,
    answer_query,
    normalize_answer_text,
    emit_semantic_content,
    user_text: str,
    parsed: dict,
    schedule_data,
    top_decks_data,
    cards_meta_data,
    card_deck_stats,
    structured_repository,
    retriever,
    api_key: str,
    event_sink,
    runtime_metadata: dict,
    stream_content: bool,
) -> AnswerResult:
    result = await answer_query(
        user_text=user_text,
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=top_decks_data,
        cards_meta_data=cards_meta_data,
        card_deck_stats=card_deck_stats,
        structured_repository=structured_repository,
        retriever=retriever,
        api_key=api_key,
        include_metadata=True,
        runtime_metadata=runtime_metadata,
        event_sink=event_sink,
        stream_content=stream_content,
    )
    assert isinstance(result, AnswerResult)
    result.answer = normalize_answer_text(result.answer)
    result.metadata.setdefault("model_stream", "unavailable")
    if event_sink is not None and event_sink.content_count == 0:
        await emit_semantic_content(event_sink, result.answer)
    return result


def query_requires_official_snapshot(parsed: dict) -> bool:
    """Return whether a parsed request needs the official weekly game snapshot.

    Removed clan-war intents are rejected locally without touching data APIs.
    In strict mode, every card, deck, ranking, or open-analysis subquery must
    receive a complete Supercell-derived snapshot rather than repository JSON.
    """
    intent = str(parsed.get("intent") or "").strip()
    if intent == "multi_intent":
        subqueries = parsed.get("subqueries")
        if not isinstance(subqueries, list) or not subqueries:
            return True
        if any(not isinstance(subquery, dict) for subquery in subqueries):
            return True
        return any(query_requires_official_snapshot(subquery) for subquery in subqueries)
    return intent not in {
        "schedule_query",
        "schedule_summary_query",
        "match_preparation_query",
        "reject",
    }


def build_external_api_unavailable_result(parsed: dict, message: str, live_metadata: dict) -> AnswerResult:
    """Return an explicit failure instead of treating a snapshot as live data."""
    return AnswerResult(
        answer=message,
        trace_id=None,
        parsed=parsed,
        plan=None,
        selected_skill=None,
        mode="unavailable",
        metadata={
            "external_api_required": True,
            "live_data": live_metadata,
            "model_stream": "unavailable",
        },
    )


def describe_parsed_request(parsed: dict) -> str:
    """Render validated routing facts without exposing private model reasoning."""
    intent = parsed.get("intent")
    if intent == "multi_intent":
        parts = [describe_parsed_request(item) for item in parsed.get("subqueries", []) if isinstance(item, dict)]
        return f"识别到 {len(parts)} 个子问题：" + "；".join(parts)
    if intent == "card_query":
        card = parsed.get("card_name") or "卡牌排行"
        metric_values = parsed.get("metrics") or ([parsed.get("metric")] if parsed.get("metric") else [])
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metrics = "、".join(metric_labels.get(metric, str(metric)) for metric in metric_values)
        return f"{card} 的{metrics or '数据'}查询"
    if intent == "card_compare_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("compare_metric"), "表现")
        return f"{' 与 '.join(names) or '两张卡牌'}的{metric}比较"
    if intent == "card_cooccurrence_query":
        names = [str(name) for name in (parsed.get("card_names") or []) if name]
        if len(names) >= 2:
            return f"{' 与 '.join(names[:2])}共同出现次数查询"
        return f"{parsed.get('card_name') or '卡牌'}的常见搭配查询"
    if intent == "card_rank_lookup_query":
        metric_labels = {
            "usage_rate": "使用率",
            "win_rate": "胜率",
            "clean_win_rate": "净胜率",
        }
        metric = metric_labels.get(parsed.get("metric"), "表现")
        return f"卡牌{metric}第 {parsed.get('rank') or '?'} 名查询"
    if intent == "deck_query":
        if parsed.get("deck_cards"):
            return "精确八卡卡组查询"
        return f"{parsed.get('card_name') or '热门'}卡组查询"
    if intent == "meta_analysis_query":
        return "当前环境与主流卡组的开放分析"
    if intent == "match_preparation_query":
        return "备战开放分析"
    if intent == "schedule_query":
        return "赛程查询"
    return "未支持的问题类型"


__all__ = [
    "AnswerExecutionContext",
    "AnswerPipelineDependencies",
    "build_external_api_unavailable_result",
    "describe_parsed_request",
    "execute_grounded_answer",
    "prepare_answer_execution_context",
    "query_requires_official_snapshot",
    "run_answer_pipeline",
]
