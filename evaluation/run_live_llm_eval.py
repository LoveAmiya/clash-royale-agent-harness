"""Live LLM parser evaluation with no local-parser fallback."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from model_gateway import generate_model_text
from app_config import OPENAI_MODEL
from evaluation.scorecard import attach_scorecard
from evaluation.contract_fixtures import sample_cards
from query_parser import PARSER_SYSTEM_PROMPT, extract_json_block, normalize_parsed_query
from skills.base import SkillContext
from skills.registry import build_default_registry


CASES = [
    ("schedule_query", "When is round 1?"), ("schedule_query", "Who plays in round 2?"),
    ("schedule_query", "Show the schedule for round 3."), ("schedule_query", "Round 4 schedule please."),
    ("schedule_query", "What matches are in round 5?"), ("schedule_query", "Who is playing next round?"),
    ("schedule_summary_query", "Summarize the remaining schedule."), ("schedule_summary_query", "How many matches are left?"),
    ("schedule_summary_query", "Give me an overview of upcoming matches."), ("schedule_summary_query", "Is the schedule crowded later on?"),
    ("schedule_summary_query", "What is the tournament schedule situation?"), ("schedule_summary_query", "Summarize future fixtures."),
    ("deck_query", "Show the top 3 decks."), ("deck_query", "Which decks are most popular?"),
    ("deck_query", "Give me the five best decks."), ("deck_query", "What deck is ranked first?"),
    ("deck_query", "List popular deck archetypes."), ("deck_query", "Show the top decks by usage."),
    ("card_query", "What is Fireball win rate?"), ("card_query", "Show Miner usage rate."),
    ("card_query", "Which cards have the highest win rate?"), ("card_query", "Top five cards by usage rate."),
    ("card_query", "How popular is Poison?"), ("card_query", "What is the clean win rate of Balloon?"),
    ("card_compare_query", "Which has a higher win rate, Fireball or Poison?"), ("card_compare_query", "Compare Miner and Hog Rider usage rate."),
    ("card_compare_query", "Is Balloon stronger than Lava Hound by win rate?"), ("card_compare_query", "Compare The Log and Arrows."),
    ("card_compare_query", "Which is used more, Skeletons or Electro Spirit?"), ("card_compare_query", "Compare Firecracker with Princess."),
    ("card_rank_lookup_query", "What usage-rate rank is The Log?"), ("card_rank_lookup_query", "Where does Fireball rank by win rate?"),
    ("card_rank_lookup_query", "What is Miner rank in the usage list?"), ("card_rank_lookup_query", "Tell me Poison's clean-win-rate ranking."),
    ("card_rank_lookup_query", "What position is Balloon in by usage?"), ("card_rank_lookup_query", "What is Hog Rider ranked at?"),
    ("meta_analysis_query", "What is the current meta and how should I counter it?"), ("meta_analysis_query", "Explain card synergies in the current environment."),
    ("meta_analysis_query", "What counters the popular decks?"), ("meta_analysis_query", "Analyze the balance and card roles."),
    ("meta_analysis_query", "How should I adapt my deck to the meta?"), ("meta_analysis_query", "Give me a matchup analysis."),
    ("match_preparation_query", "How should I prepare for the next match?"), ("match_preparation_query", "Recommend a practice deck for the next round."),
    ("match_preparation_query", "What should the team train before the next game?"), ("match_preparation_query", "Give a preparation plan for the upcoming match."),
    ("reject", "What will the weather be tomorrow?"), ("reject", "Write me a poem about the ocean."),
    ("reject", "Who won the football match last night?"), ("reject", "Recommend a restaurant near me."),
]


async def evaluate_case(index: int, expected_intent: str, question: str, cards: list[dict], registry, api_key: str, semaphore: asyncio.Semaphore, timeout_seconds: float) -> dict:
    started = time.perf_counter()
    question_identity = {
        "question_hash": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "question_length": len(question),
    }
    async with semaphore:
        try:
            text = await asyncio.wait_for(
                generate_model_text(api_key=api_key, instructions=PARSER_SYSTEM_PROMPT, input_text=question),
                timeout=timeout_seconds,
            )
            parsed_raw = extract_json_block(text)
            if parsed_raw is None:
                raise ValueError("model output was not a JSON object")
            parsed = normalize_parsed_query(parsed_raw, question, cards)
            skill = registry.select(SkillContext(user_text=question, parsed=parsed, schedule_data=[], top_decks_data=[], cards_meta_data=cards, metadata={}))
            actual = parsed.get("intent")
            return {"id": index, **question_identity, "expected_intent": expected_intent, "parsed_intent": actual, "selected_skill": skill.name if skill else None, "success": actual == expected_intent, "error": None, "elapsed_seconds": round(time.perf_counter() - started, 3)}
        except Exception as exc:
            return {"id": index, **question_identity, "expected_intent": expected_intent, "parsed_intent": None, "selected_skill": None, "success": False, "error": f"{type(exc).__name__}: {exc}", "elapsed_seconds": round(time.perf_counter() - started, 3)}


def build_report(results: list[dict], case_count: int, args: argparse.Namespace, status: str) -> dict:
    passed = sum(item["success"] for item in results)
    report = {
        "benchmark": "Live structured-query parser",
        "status": status,
        "case_count": case_count,
        "completed_cases": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "accuracy": passed / len(results) if results else 0,
        "timeout_seconds": args.timeout_seconds,
        "concurrency": args.concurrency,
        "results": results,
    }
    attach_scorecard(
        report,
        dimensions={
            "model": OPENAI_MODEL,
            "prompt_hash": hashlib.sha256(PARSER_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        },
        source="live_llm_parser",
    )
    return report


def write_report(report_path: Path, results: list[dict], case_count: int, args: argparse.Namespace, status: str) -> dict:
    report = build_report(results, case_count, args, status)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def run(args: argparse.Namespace, report_path: Path) -> dict:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is required; refusing to use the fallback parser.")
    cards = sample_cards()
    registry = build_default_registry()
    cases = CASES[: args.limit] if args.limit is not None else CASES
    results_by_id: dict[int, dict] = {}
    if args.resume and report_path.exists():
        previous = json.loads(report_path.read_text(encoding="utf-8"))
        for result in previous.get("results", []):
            if isinstance(result.get("id"), int):
                sanitized = dict(result)
                prior_question = sanitized.pop("question", None)
                if isinstance(prior_question, str):
                    sanitized.setdefault("question_hash", hashlib.sha256(prior_question.encode("utf-8")).hexdigest())
                    sanitized.setdefault("question_length", len(prior_question))
                results_by_id[result["id"]] = sanitized

    semaphore = asyncio.Semaphore(args.concurrency)
    for index, (intent, question) in enumerate(cases, 1):
        if index in results_by_id:
            continue
        results_by_id[index] = await evaluate_case(index, intent, question, cards, registry, api_key, semaphore, args.timeout_seconds)
        ordered_results = [results_by_id[item] for item in sorted(results_by_id)]
        write_report(report_path, ordered_results, len(cases), args, "running")

    results = [results_by_id[item] for item in sorted(results_by_id)]
    return build_report(results, len(cases), args, "completed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--report", default="evaluation/live_llm_report.json")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.concurrency <= 0 or args.timeout_seconds <= 0 or (args.limit is not None and args.limit <= 0):
        raise SystemExit("--concurrency, --timeout-seconds, and --limit must be positive")
    report_path = Path(args.report)
    report = asyncio.run(run(args, report_path))
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("benchmark", "case_count", "passed", "failed", "accuracy")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
