"""Multi-intent answer orchestration for QA queries."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass(slots=True)
class MultiIntentDependencies:
    answer_result_cls: Callable[..., Any]
    execute_subquery: Callable[..., Awaitable[dict[str, Any]]] | None = None
    recorder: Any | None = None
    subquery_semantic_key: Callable[[dict[str, Any]], Any] | None = None
    skill_context_cls: Callable[..., Any] | None = None
    skill_executor: Any | None = None
    planner: Any | None = None
    skill_registry: Any | None = None
    subquery_needs_rag: Callable[[dict[str, Any]], bool] | None = None
    subquery_title: Callable[[dict[str, Any]], str] | None = None
    subquery_user_text: Callable[[dict[str, Any], str], str] | None = None
    logger: Any | None = None
    perf_counter: Callable[[], float] = time.perf_counter


def compose_multi_intent_answer(results: list[dict[str, Any]]) -> str:
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
    parsed: dict[str, Any],
    schedule_data: list[dict[str, Any]],
    top_decks_data: list[dict[str, Any]],
    cards_meta_data: list[dict[str, Any]],
    retriever: Any | None,
    api_key: str,
    trace_id: str,
    dependencies: MultiIntentDependencies,
    runtime_metadata: dict[str, Any] | None = None,
    card_deck_stats: dict[str, list[dict[str, Any]]] | None = None,
    structured_repository: Any = None,
    event_sink: Any | None = None,
    stream_content: bool = False,
) -> dict[str, Any]:
    required = {
        "skill_context_cls": dependencies.skill_context_cls,
        "skill_executor": dependencies.skill_executor,
        "planner": dependencies.planner,
        "skill_registry": dependencies.skill_registry,
        "subquery_needs_rag": dependencies.subquery_needs_rag,
        "subquery_title": dependencies.subquery_title,
        "subquery_user_text": dependencies.subquery_user_text,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(f"Missing multi-intent dependencies: {', '.join(missing)}")

    subquery_id = str(parsed.get("id") or "q")
    started_at = dependencies.perf_counter()
    context = dependencies.skill_context_cls(
        user_text=dependencies.subquery_user_text(parsed, user_text),
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
    plan = dependencies.planner.build_plan(context)
    if plan is not None:
        context.metadata["plan"] = plan.to_dict()

    if event_sink is not None:
        candidate = dependencies.skill_registry.resolve(parsed)
        await event_sink.execution(
            step_id=f"{subquery_id}.route",
            phase="route",
            status="running",
            subquery_id=subquery_id,
            title=f"正在执行{dependencies.subquery_title(parsed)}",
            detail=f"路由到 {candidate.name if candidate else '未匹配 Skill'}。",
            operation="intent.route",
            parameters={
                "mode": parsed.get("intent"),
                "scope": (runtime_metadata or {}).get("dataset_scope"),
            },
            rationale=f"将用户问题理解为“{dependencies.subquery_title(parsed)}”并选择对应执行能力。",
            boundaries=["这里展示的是已执行的路由摘要，不是模型的私有思维链。"],
        )

    unavailable = None
    if dependencies.subquery_needs_rag(parsed) and not api_key:
        unavailable = "OPENAI_API_KEY is not configured"
    elif dependencies.subquery_needs_rag(parsed) and retriever is None:
        unavailable = "RAG retriever is unavailable"

    try:
        answer = await dependencies.skill_executor.execute(context)
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
                title=f"已完成{dependencies.subquery_title(parsed)}",
                detail=f"使用 {context.metadata.get('selected_skill') or '未匹配 Skill'}。",
                elapsed_ms=int((dependencies.perf_counter() - started_at) * 1000),
                operation="intent.route",
                parameters={
                    "mode": parsed.get("intent"),
                    "scope": (runtime_metadata or {}).get("dataset_scope"),
                },
                evidence=[f"执行路径：{context.metadata.get('selected_skill') or '未匹配 Skill'}。"],
            )
        return {
            "id": subquery_id,
            "title": dependencies.subquery_title(parsed),
            "parsed": parsed,
            "plan": context.metadata.get("plan"),
            "selected_skill": context.metadata.get("selected_skill"),
            "mode": context.metadata.get("mode"),
            "status": status,
            "answer": answer,
            "metadata": dict(context.metadata),
            "error": unavailable,
            "latency_ms": int((dependencies.perf_counter() - started_at) * 1000),
        }
    except Exception as exc:
        if dependencies.logger is not None:
            dependencies.logger.exception("subquery failed id=%s intent=%s", subquery_id, parsed.get("intent"))
        if event_sink is not None:
            await event_sink.execution(
                step_id=f"{subquery_id}.route",
                phase="route",
                status="failed",
                subquery_id=subquery_id,
                title=f"{dependencies.subquery_title(parsed)}未完成",
                detail=type(exc).__name__,
                elapsed_ms=int((dependencies.perf_counter() - started_at) * 1000),
            )
        return {
            "id": subquery_id,
            "title": dependencies.subquery_title(parsed),
            "parsed": parsed,
            "plan": context.metadata.get("plan"),
            "selected_skill": context.metadata.get("selected_skill"),
            "mode": context.metadata.get("mode"),
            "status": "failed",
            "answer": "",
            "metadata": dict(context.metadata),
            "error": str(exc),
            "latency_ms": int((dependencies.perf_counter() - started_at) * 1000),
        }


async def answer_multi_intent_query(
    *,
    user_text: str,
    parsed: dict[str, Any],
    schedule_data: list[dict[str, Any]],
    top_decks_data: list[dict[str, Any]],
    cards_meta_data: list[dict[str, Any]],
    retriever: Any | None,
    api_key: str,
    dependencies: MultiIntentDependencies,
    runtime_metadata: dict[str, Any] | None = None,
    card_deck_stats: dict[str, list[dict[str, Any]]] | None = None,
    structured_repository: Any = None,
    event_sink: Any | None = None,
    stream_content: bool = False,
) -> Any:
    if dependencies.recorder is None:
        raise ValueError("Missing multi-intent recorder dependency")
    if dependencies.execute_subquery is None:
        raise ValueError("Missing multi-intent execute_subquery dependency")
    if dependencies.subquery_semantic_key is None:
        raise ValueError("Missing multi-intent subquery_semantic_key dependency")

    trace_id = dependencies.recorder.new_trace_id()
    started_at = dependencies.perf_counter()
    subqueries: list[dict[str, Any]] = []
    seen_subqueries: set[Any] = set()
    for item in parsed.get("subqueries", []):
        if not isinstance(item, dict):
            continue
        key = dependencies.subquery_semantic_key(item)
        if key in seen_subqueries:
            continue
        seen_subqueries.add(key)
        subqueries.append(item)
    results = await asyncio.gather(
        *[
            dependencies.execute_subquery(
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
        "total_latency_ms": int((dependencies.perf_counter() - started_at) * 1000),
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
    return dependencies.answer_result_cls(
        answer=compose_multi_intent_answer(results),
        trace_id=trace_id,
        parsed=parsed,
        plan=plan,
        selected_skill="MultiIntentOrchestrator",
        mode="mixed",
        metadata=metadata,
        sub_results=results,
    )


__all__ = [
    "MultiIntentDependencies",
    "answer_multi_intent_query",
    "compose_multi_intent_answer",
    "execute_subquery",
]
