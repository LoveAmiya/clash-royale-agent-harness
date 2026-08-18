"""Model-parser orchestration around deterministic local parser dependencies."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable


ParseFunction = Callable[[str, list[dict]], dict]
JsonExtractor = Callable[[str], dict | None]
MetadataBuilder = Callable[..., dict]
MetadataMerger = Callable[[dict, dict], dict]
SemanticKeyBuilder = Callable[[dict], tuple]
ModelTextGenerator = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class ParserOrchestrationDependencies:
    """Stable adapters needed to combine local and model parser results."""

    fallback_parse_multi_intent: ParseFunction
    extract_json_block: JsonExtractor
    normalize_multi_intent_query: ParseFunction
    merge_parse_metadata: MetadataMerger
    build_parse_metadata: MetadataBuilder
    subquery_semantic_key: SemanticKeyBuilder
    generate_model_text: ModelTextGenerator
    parser_system_prompt: str
    parser_reasoning_effort: str
    parser_timeout_seconds: float
    high_confidence: str
    medium_confidence: str
    low_confidence: str
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))


async def parse_user_query_with_model(
    user_text: str,
    cards_meta_data: list[dict],
    api_key: str | None,
    dependencies: ParserOrchestrationDependencies,
) -> dict:
    """Combine a deterministic parse with an optional validated model parse."""
    local_parsed = dependencies.fallback_parse_multi_intent(user_text, cards_meta_data)
    if not api_key:
        dependencies.logger.warning("no api key available, using fallback parser result")
        return {
            **local_parsed,
            "model_parser_attempted": False,
            "model_parser_status": "not_configured",
        }

    def reconciled_local_parse(reason: str) -> dict:
        return {
            **dependencies.merge_parse_metadata(
                local_parsed,
                dependencies.build_parse_metadata(
                    parse_source="llm_parser",
                    parse_confidence=local_parsed.get(
                        "parse_confidence", dependencies.high_confidence
                    ),
                    parse_reason=reason,
                ),
            ),
            "model_parser_attempted": True,
            "model_parser_status": "validated_reconciled",
        }

    def validated_fallback(reason: str, status: str) -> dict:
        confidence = local_parsed.get("parse_confidence", dependencies.low_confidence)
        can_continue = (
            local_parsed.get("intent") != "reject"
            and confidence in {dependencies.high_confidence, dependencies.medium_confidence}
        )
        return {
            **dependencies.merge_parse_metadata(
                local_parsed,
                dependencies.build_parse_metadata(
                    parse_source=(
                        "validated_fallback"
                        if can_continue
                        else local_parsed.get("parse_source", "local_rule")
                    ),
                    parse_confidence=confidence,
                    parse_reason=reason,
                ),
            ),
            "model_parser_attempted": True,
            "model_parser_status": status,
        }

    try:
        parse_text = await asyncio.wait_for(
            dependencies.generate_model_text(
                api_key=api_key,
                instructions=dependencies.parser_system_prompt,
                input_text=user_text,
                reasoning_effort=dependencies.parser_reasoning_effort,
            ),
            timeout=dependencies.parser_timeout_seconds,
        )
        dependencies.logger.debug("parser returned public text chars=%s", len(parse_text))

        parsed = dependencies.extract_json_block(parse_text)
        if parsed is None:
            dependencies.logger.warning("parser returned non-json output, using fallback parser")
            return validated_fallback(
                "llm parser returned non-json output; kept locally validated structured parse",
                "invalid_response",
            )

        normalized = dependencies.normalize_multi_intent_query(
            parsed, user_text, cards_meta_data
        )
        local_intents = [item.get("intent") for item in local_parsed.get("subqueries", [])]
        normalized_intents = [item.get("intent") for item in normalized.get("subqueries", [])]
        local_semantic_keys = [
            dependencies.subquery_semantic_key(item)
            for item in local_parsed.get("subqueries", [])
            if isinstance(item, dict)
        ]
        normalized_semantic_keys = [
            dependencies.subquery_semantic_key(item)
            for item in normalized.get("subqueries", [])
            if isinstance(item, dict)
        ]
        if (
            local_parsed.get("intent") != "multi_intent"
            and local_parsed.get("intent") != "reject"
            and local_parsed.get("parse_confidence") == dependencies.high_confidence
            and normalized.get("intent") != local_parsed.get("intent")
        ):
            return reconciled_local_parse(
                "gpt-5.5 parser output was reconciled to the high-confidence local structured intent"
            )
        if local_parsed.get("intent") == "multi_intent" and (
            normalized.get("intent") != "multi_intent"
            or normalized_intents != local_intents
            or normalized_semantic_keys != local_semantic_keys
        ):
            return reconciled_local_parse(
                "gpt-5.5 parser output was reconciled to the high-confidence local multi-intent decomposition"
            )
        if normalized.get("intent") == "reject" and local_parsed.get("intent") != "reject":
            return reconciled_local_parse(
                "gpt-5.5 parser output was reconciled to the locally validated supported route"
            )
        return {
            **dependencies.merge_parse_metadata(
                normalized,
                dependencies.build_parse_metadata(
                    parse_source="llm_parser",
                    parse_confidence=dependencies.high_confidence,
                    parse_reason="gpt-5.5 structured parser output validated locally",
                ),
            ),
            "model_parser_attempted": True,
            "model_parser_status": "validated",
        }
    except Exception as exc:
        status = "timeout" if isinstance(exc, TimeoutError) else "error"
        dependencies.logger.warning(
            "parser agent failed, using validated fallback error_type=%s",
            type(exc).__name__,
        )
        return validated_fallback(
            f"llm parser failed; kept locally validated structured parse: {type(exc).__name__}",
            status,
        )


__all__ = ["ParserOrchestrationDependencies", "parse_user_query_with_model"]
