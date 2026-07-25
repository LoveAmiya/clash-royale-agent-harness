import asyncio
import argparse
from datetime import datetime, timezone
import json
import inspect
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.metrics import summarize_results
from query_parser import fallback_parse_multi_intent
from skills.base import SkillContext
from skills.registry import build_default_registry


DATA_DIR = ROOT / "data"
CASES_FILE = ROOT / "evaluation" / "cases.jsonl"
REPORTS_DIR = ROOT / "evaluation" / "reports"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases() -> list[dict]:
    cases = []
    for line in CASES_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cases.append(json.loads(stripped))
    return cases


def build_context(case: dict, parsed: dict, schedule_data: list[dict], deck_data: list[dict], card_data: list[dict]) -> SkillContext:
    return SkillContext(
        user_text=case["question"],
        parsed=parsed,
        schedule_data=schedule_data,
        top_decks_data=deck_data,
        cards_meta_data=card_data,
        metadata={},
    )


def should_skip_execution(skill_name: str | None, case: dict) -> bool:
    return skill_name in {"RAGEvidenceSkill", "EvidenceSynthesisSkill"} and case.get("optional", False)


def validate_case_expectations(case: dict, result: dict) -> dict[str, str]:
    """Return every failed assertion so an evaluation report is actionable."""
    errors: dict[str, str] = {}

    expected_intent = case.get("expected_intent")
    if expected_intent is not None and result["parsed_intent"] != expected_intent:
        errors["parsed_intent"] = (
            f"expected {expected_intent!r}, got {result['parsed_intent']!r}"
        )

    if "expected_skill" in case and result["selected_skill"] != case["expected_skill"]:
        errors["selected_skill"] = (
            f"expected {case['expected_skill']!r}, got {result['selected_skill']!r}"
        )

    parsed = result["parsed"]
    for key, expected in case.get("expected_fields", {}).items():
        actual = parsed.get(key)
        if actual != expected:
            errors[key] = f"expected {expected!r}, got {actual!r}"

    if "expected_subqueries" in case:
        expected_subqueries = case["expected_subqueries"]
        actual_subqueries = result.get("parsed_subqueries", [])
        if actual_subqueries != expected_subqueries:
            errors["parsed_subqueries"] = (
                f"expected {expected_subqueries!r}, got {actual_subqueries!r}"
            )

    if not result["skipped"]:
        answer = result["answer"]
        missing_fragments = [
            fragment
            for fragment in case.get("answer_contains", [])
            if fragment not in answer
        ]
        if missing_fragments:
            errors["answer_contains"] = (
                "missing answer fragments: " + repr(missing_fragments)
            )

    return errors


def evaluate_case(case: dict, registry, schedule_data, deck_data, card_data) -> dict:
    parsed = fallback_parse_multi_intent(case["question"], card_data)
    context = build_context(case, parsed, schedule_data, deck_data, card_data)
    selected_skill = registry.select(context)
    selected_skill_name = selected_skill.name if selected_skill is not None else None

    result = {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "expected_intent": case.get("expected_intent"),
        "expected_skill": case.get("expected_skill"),
        "expected_fields": case.get("expected_fields", {}),
        "answer_contains": case.get("answer_contains", []),
        "parsed_intent": parsed.get("intent"),
        "parsed": parsed,
        "selected_skill": selected_skill_name,
        "answer": "",
        "success": True,
        "error": None,
        "errors": {},
        "skipped": False,
    }

    if parsed.get("intent") == "multi_intent":
        parsed_subqueries = [
            {
                "intent": item.get("intent"),
                "card_name": item.get("card_name"),
                "metrics": item.get("metrics"),
            }
            for item in parsed.get("subqueries", [])
        ]
        expected_subqueries = case.get("expected_subqueries", [])
        result["parsed_subqueries"] = parsed_subqueries
        result["expected_subqueries"] = expected_subqueries
        result["errors"] = validate_case_expectations(case, result)
        result["success"] = not result["errors"]
        result["error"] = "; ".join(result["errors"].values()) or None
        return result

    if should_skip_execution(selected_skill_name, case):
        result["skipped"] = True
        result["errors"] = validate_case_expectations(case, result)
        result["success"] = not result["errors"]
        result["error"] = (
            "; ".join(result["errors"].values())
            if result["errors"]
            else "optional RAG case skipped by default"
        )
        return result

    if selected_skill is None:
        result["errors"] = validate_case_expectations(case, result)
        result["success"] = not result["errors"]
        result["error"] = "; ".join(result["errors"].values()) or None
        return result

    try:
        answer = selected_skill.run(context)
        if inspect.isawaitable(answer):
            answer = asyncio.run(answer)
        result["answer"] = answer
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc)

    expectation_errors = validate_case_expectations(case, result)
    if result["error"]:
        expectation_errors["execution"] = result["error"]
    result["errors"] = expectation_errors
    result["success"] = not expectation_errors
    result["error"] = "; ".join(expectation_errors.values()) or None

    return result


def default_report_path() -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPORTS_DIR / f"evaluation-{timestamp}.json"


def write_report(report: dict[str, Any], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_evaluation(
    *,
    cases: list[dict] | None = None,
    report_path: Path | None = None,
    registry=None,
    schedule_data: list[dict] | None = None,
    deck_data: list[dict] | None = None,
    card_data: list[dict] | None = None,
) -> dict[str, Any]:
    schedule_data = schedule_data if schedule_data is not None else load_json(DATA_DIR / "schedule.json")
    deck_data = deck_data if deck_data is not None else load_json(DATA_DIR / "top_decks.json")
    card_data = card_data if card_data is not None else load_json(DATA_DIR / "cards_meta.json")
    cases = cases if cases is not None else load_cases()
    registry = registry if registry is not None else build_default_registry()

    results = [
        evaluate_case(case, registry, schedule_data, deck_data, card_data)
        for case in cases
    ]
    summary = summarize_results(results)
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases_file": str(CASES_FILE.relative_to(ROOT)),
        "summary": summary,
        "results": results,
        "failures": [item for item in results if not item["success"]],
    }
    if report_path is not None:
        write_report(report, report_path)
    return report


def print_report(report: dict[str, Any]) -> None:
    print("=== Eval Summary ===")
    for key, value in report["summary"].items():
        print(f"{key}: {value}")

    print("\n=== Case Results ===")
    for item in report["results"]:
        status = "SKIPPED" if item["skipped"] else ("OK" if item["success"] else "FAILED")
        print(f"{item['id']}: {status} | intent={item['parsed_intent']} | skill={item['selected_skill']}")
        if item["error"]:
            print(f"  error: {item['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic Clash Royale evaluation corpus.")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON report path. Defaults to a new timestamped file under evaluation/reports/.",
    )
    args = parser.parse_args(argv)
    report_path = args.report or default_report_path()
    report = run_evaluation(report_path=report_path)
    print_report(report)
    print(f"\nEvaluation report: {report_path}")
    return 1 if report["summary"]["failed_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
