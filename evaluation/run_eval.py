import json
from pathlib import Path

from evaluation.metrics import summarize_results
from query_parser import fallback_parse_query
from skills.base import SkillContext
from skills.registry import build_default_registry


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CASES_FILE = ROOT / "evaluation" / "cases.jsonl"


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
    return skill_name == "RAGEvidenceSkill" and case.get("optional", False)


def evaluate_case(case: dict, registry, schedule_data, deck_data, card_data) -> dict:
    parsed = fallback_parse_query(case["question"], card_data)
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
        "skipped": False,
    }

    if should_skip_execution(selected_skill_name, case):
        result["skipped"] = True
        result["success"] = True
        result["error"] = "optional RAG case skipped by default"
        return result

    if selected_skill is None:
        return result

    try:
        answer = selected_skill.run(context)
        if hasattr(answer, "__await__"):
            result["skipped"] = True
            result["error"] = "async skill execution not supported in default local eval"
            return result
        result["answer"] = answer
    except Exception as exc:
        result["success"] = False
        result["error"] = str(exc)

    return result


def main():
    schedule_data = load_json(DATA_DIR / "schedule.json")
    deck_data = load_json(DATA_DIR / "top_decks.json")
    card_data = load_json(DATA_DIR / "cards_meta.json")
    cases = load_cases()
    registry = build_default_registry()

    results = [
        evaluate_case(case, registry, schedule_data, deck_data, card_data)
        for case in cases
    ]

    summary = summarize_results(results)
    print("=== Eval Summary ===")
    for key, value in summary.items():
        print(f"{key}: {value}")

    print("\n=== Case Results ===")
    for item in results:
        status = "SKIPPED" if item["skipped"] else ("OK" if item["success"] else "FAILED")
        print(f"{item['id']}: {status} | intent={item['parsed_intent']} | skill={item['selected_skill']}")
        if item["error"]:
            print(f"  error: {item['error']}")


if __name__ == "__main__":
    main()
