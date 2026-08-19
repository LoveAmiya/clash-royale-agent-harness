from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from query_answering import AnswerResult


@dataclass(frozen=True)
class AnswerParseDependencies:
    detect_unsupported_analysis_request: Callable[[str], dict | None]
    build_analysis_boundary_answer: Callable[[dict], str]
    build_external_api_unavailable_result: Callable[[dict, str, dict], AnswerResult]
    parse_user_query: Callable[[str, list[dict], str | None], Any]
    apply_selected_entity_mode: Callable[[dict, str], dict]
    describe_parsed_request: Callable[[dict], str]
    logger: Any
    high_confidence: str
    medium_confidence: str
    model_name: str


@dataclass(frozen=True)
class ParsedAnswerRequest:
    parsed: dict
    deck_mode: str
    entity_mode: str
    parser_api: dict


async def parse_answer_request(
    *,
    dependencies: AnswerParseDependencies,
    user_text: str,
    bootstrap_cards_meta_data: list[dict],
    api_key: str | None,
    external_api_required: bool,
    intent_hint: str | None,
    deck_mode: str,
    entity_mode: str,
    request_id: str | None,
    event_sink: Any = None,
) -> ParsedAnswerRequest | AnswerResult:
    if event_sink is not None:
        await event_sink.execution(
            step_id="parse",
            phase="parse",
            status="running",
            title="正在解析问题",
            detail=(
                "使用页面的已验证功能契约进入环境 RAG 分析。"
                if intent_hint == "meta_analysis_query"
                else "使用模型 API 识别可执行意图，不展示内部推理。"
            ),
        )

    analysis_boundary = dependencies.detect_unsupported_analysis_request(user_text)
    if analysis_boundary is not None:
        parsed = {
            "intent": "reject",
            "parse_source": "analysis_boundary",
            "parse_confidence": dependencies.high_confidence,
            "parse_reason": "request requires evidence or a model not provided by the current snapshot",
            "boundary_code": analysis_boundary["code"],
            "model_parser_attempted": False,
            "model_parser_status": "not_required",
        }
        if event_sink is not None:
            await event_sink.execution(
                step_id="parse",
                phase="parse",
                status="completed",
                title="已确认数据边界",
                detail="该问题要求当前观测快照无法支持的预测、精确概率、因果效果或历史趋势。",
            )
        dependencies.logger.info(
            "request rejected by analysis boundary request_id=%s boundary=%s",
            request_id,
            analysis_boundary["code"],
        )
        return AnswerResult(
            answer=dependencies.build_analysis_boundary_answer(analysis_boundary),
            trace_id=None,
            parsed=parsed,
            plan=None,
            selected_skill=None,
            mode="boundary_reject",
            metadata={
                "boundary": analysis_boundary,
                "model_parser_attempted": False,
                "data_context": {"snapshot": "observational_only"},
            },
        )

    if external_api_required and not api_key:
        parsed = {
            "intent": "reject",
            "parse_source": "model_api_unavailable",
            "parse_reason": "OPENAI_API_KEY is not configured",
        }
        return dependencies.build_external_api_unavailable_result(
            parsed,
            "Model API is unavailable. Strict external API mode will not use local parsing as a substitute.",
            {"status": "not_checked"},
        )

    if intent_hint == "meta_analysis_query":
        parsed = {
            "intent": "meta_analysis_query",
            "parse_source": "interface_contract",
            "parse_confidence": dependencies.high_confidence,
            "parse_reason": "validated environment-analysis page contract",
            "model_parser_attempted": False,
            "model_parser_status": "not_required",
        }
    else:
        parsed = await dependencies.parse_user_query(user_text, bootstrap_cards_meta_data, api_key)
    parsed = dependencies.apply_selected_entity_mode(parsed, entity_mode)
    parsed_subqueries = parsed.get("subqueries") if isinstance(parsed.get("subqueries"), list) else []
    if parsed.get("entity_mode") == "loadout_entity" or any(
        subquery.get("entity_mode") == "loadout_entity"
        for subquery in parsed_subqueries
        if isinstance(subquery, dict)
    ):
        entity_mode = "loadout_entity"
        deck_mode = "full_loadout"
    dependencies.logger.info(
        "request parsed request_id=%s intent=%s source=%s subqueries=%s",
        request_id,
        parsed.get("intent"),
        parsed.get("parse_source"),
        len(parsed.get("subqueries", [])) if isinstance(parsed.get("subqueries"), list) else 0,
    )
    if event_sink is not None:
        await event_sink.execution(
            step_id="parse",
            phase="parse",
            status="completed",
            title="已解析问题",
            detail=dependencies.describe_parsed_request(parsed),
        )

    parse_source = parsed.get("parse_source")
    validated_fallback = (
        parse_source == "validated_fallback"
        and parsed.get("model_parser_attempted") is True
        and parsed.get("parse_confidence") in {dependencies.high_confidence, dependencies.medium_confidence}
        and parsed.get("intent") != "reject"
    )
    parser_status = (
        "api"
        if parse_source == "llm_parser"
        else "interface_contract"
        if parse_source == "interface_contract"
        else "degraded"
        if validated_fallback
        else "fallback"
    )
    parser_api = {
        "status": parser_status,
        "parse_source": parsed.get("parse_source"),
        "model_status": parsed.get("model_parser_status"),
        "model": dependencies.model_name,
    }
    if external_api_required and parser_api["status"] not in {"api", "degraded", "interface_contract"}:
        unavailable = dependencies.build_external_api_unavailable_result(
            parsed,
            "Model parser did not return a validated API result. Strict external API mode will not use local parsing as a substitute.",
            {"status": "not_checked"},
        )
        unavailable.metadata["parser_api"] = {**parser_api, "status": "unavailable"}
        return unavailable
    return ParsedAnswerRequest(
        parsed=parsed,
        deck_mode=deck_mode,
        entity_mode=entity_mode,
        parser_api=parser_api,
    )


__all__ = ["AnswerParseDependencies", "ParsedAnswerRequest", "parse_answer_request"]
