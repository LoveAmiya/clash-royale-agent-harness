def parser_intent_accuracy(results: list[dict]) -> float:
    comparable = [item for item in results if item.get("expected_intent") is not None and not item.get("skipped")]
    if not comparable:
        return 0.0
    matched = sum(1 for item in comparable if item.get("parsed_intent") == item.get("expected_intent"))
    return matched / len(comparable)


def parser_field_accuracy(results: list[dict]) -> float:
    total_fields = 0
    matched_fields = 0
    for item in results:
        if item.get("skipped"):
            continue
        expected_fields = item.get("expected_fields") or {}
        parsed = item.get("parsed") or {}
        for key, value in expected_fields.items():
            total_fields += 1
            if parsed.get(key) == value:
                matched_fields += 1
    if total_fields == 0:
        return 0.0
    return matched_fields / total_fields


def skill_routing_accuracy(results: list[dict]) -> float:
    comparable = [item for item in results if item.get("expected_skill") is not None and not item.get("skipped")]
    if not comparable:
        return 0.0
    matched = sum(1 for item in comparable if item.get("selected_skill") == item.get("expected_skill"))
    return matched / len(comparable)


def answer_contains_accuracy(results: list[dict]) -> float:
    total_checks = 0
    matched_checks = 0
    for item in results:
        if item.get("skipped"):
            continue
        answer = item.get("answer") or ""
        for fragment in item.get("answer_contains", []):
            total_checks += 1
            if fragment in answer:
                matched_checks += 1
    if total_checks == 0:
        return 0.0
    return matched_checks / total_checks


def failure_rate(results: list[dict]) -> float:
    comparable = [item for item in results if not item.get("skipped")]
    if not comparable:
        return 0.0
    failed = sum(1 for item in comparable if not item.get("success"))
    return failed / len(comparable)


def summarize_results(results: list[dict]) -> dict:
    total = len(results)
    skipped = sum(1 for item in results if item.get("skipped"))
    return {
        "total_cases": total,
        "skipped_cases": skipped,
        "parser_intent_accuracy": parser_intent_accuracy(results),
        "parser_field_accuracy": parser_field_accuracy(results),
        "skill_routing_accuracy": skill_routing_accuracy(results),
        "answer_contains_accuracy": answer_contains_accuracy(results),
        "failure_rate": failure_rate(results),
    }
