"""Structured single-intent answer orchestration.

This module owns the direct/structured answer context setup. RAG retrieval and
evidence synthesis stay in their existing builders; callers inject the skill
executor and planner so root compatibility modules can keep their patch points.
"""

from dataclasses import dataclass
from typing import Any

from skills.base import SkillContext


@dataclass(slots=True)
class StructuredAnswerDependencies:
    skill_executor: Any
    planner: Any


@dataclass(slots=True)
class StructuredAnswerExecution:
    answer: str | None
    trace_id: str | None
    plan: dict | None
    selected_skill: str | None
    mode: str | None
    metadata: dict


async def answer_structured_query(
    *,
    user_text: str,
    parsed: dict,
    schedule_data: list[dict],
    top_decks_data: list[dict],
    cards_meta_data: list[dict],
    retriever: Any | None,
    api_key: str,
    runtime_metadata: dict | None = None,
    card_deck_stats: dict[str, list[dict]] | None = None,
    structured_repository: Any | None = None,
    event_sink: Any | None = None,
    stream_content: bool = True,
    fallback_answer: str | None = None,
    dependencies: StructuredAnswerDependencies | None = None,
) -> StructuredAnswerExecution:
    if dependencies is None:
        raise ValueError("StructuredAnswerDependencies is required")

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
    plan = dependencies.planner.build_plan(context)
    if plan is not None:
        context.metadata["plan"] = plan.to_dict()

    answer = await dependencies.skill_executor.execute(context)
    if answer is None:
        answer = fallback_answer
        context.metadata.setdefault("mode", "fallback")

    return StructuredAnswerExecution(
        answer=answer,
        trace_id=context.metadata.get("trace_id"),
        plan=context.metadata.get("plan"),
        selected_skill=context.metadata.get("selected_skill"),
        mode=context.metadata.get("mode"),
        metadata=dict(context.metadata),
    )


__all__ = [
    "StructuredAnswerDependencies",
    "StructuredAnswerExecution",
    "answer_structured_query",
]
