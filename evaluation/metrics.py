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


def case_success_rate(results: list[dict]) -> float:
    comparable = [item for item in results if not item.get("skipped")]
    if not comparable:
        return 0.0
    passed = sum(1 for item in comparable if item.get("success"))
    return passed / len(comparable)


def category_summary(results: list[dict]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for item in results:
        category = item.get("category", "uncategorized")
        bucket = summary.setdefault(category, {"total": 0, "passed": 0, "failed": 0, "skipped": 0})
        bucket["total"] += 1
        if item.get("skipped"):
            bucket["skipped"] += 1
        elif item.get("success"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    return summary


def multi_subquery_accuracy(results: list[dict]) -> float:
    comparable = [item for item in results if item.get("expected_subqueries") and not item.get("skipped")]
    if not comparable:
        return 0.0
    matched = sum(item.get("expected_subqueries") == item.get("parsed_subqueries") for item in comparable)
    return matched / len(comparable)


def summarize_results(results: list[dict]) -> dict:
    total = len(results)
    skipped = sum(1 for item in results if item.get("skipped"))
    failed = sum(1 for item in results if not item.get("skipped") and not item.get("success"))
    return {
        "total_cases": total,
        "skipped_cases": skipped,
        "passed_cases": total - skipped - failed,
        "failed_cases": failed,
        "parser_intent_accuracy": parser_intent_accuracy(results),
        "parser_field_accuracy": parser_field_accuracy(results),
        "skill_routing_accuracy": skill_routing_accuracy(results),
        "answer_contains_accuracy": answer_contains_accuracy(results),
        "case_success_rate": case_success_rate(results),
        "failure_rate": failure_rate(results),
        "multi_subquery_accuracy": multi_subquery_accuracy(results),
        "categories": category_summary(results),
    }


def _ratio(results: list[dict], numerator: str, denominator: str) -> float:
    total_denominator = sum(max(0, int(item.get(denominator) or 0)) for item in results)
    if total_denominator <= 0:
        return 0.0
    total_numerator = sum(max(0, int(item.get(numerator) or 0)) for item in results)
    return total_numerator / total_denominator


def _mean(results: list[dict], field: str) -> float:
    values = [float(item[field]) for item in results if item.get(field) is not None]
    return sum(values) / len(values) if values else 0.0


def build_scorecard(results: list[dict], *, dimensions: dict | None = None) -> dict:
    """Aggregate existing benchmark outputs into one regression-comparable scorecard."""
    comparable = [item for item in results if not item.get("skipped")]
    refusal_values = [bool(item["refusal_correct"]) for item in comparable if "refusal_correct" in item]
    boundary_cases = [item for item in comparable if "boundary_violations" in item]
    return {
        "case_count": len(comparable),
        "retrieval_recall": _ratio(comparable, "retrieval_relevant", "retrieval_expected"),
        "assertion_support_rate": _ratio(comparable, "assertions_supported", "assertions_total"),
        "citation_precision": _ratio(comparable, "citations_correct", "citations_total"),
        "refusal_accuracy": (
            sum(refusal_values) / len(refusal_values) if refusal_values else 0.0
        ),
        "boundary_violation_rate": (
            sum(int(item.get("boundary_violations") or 0) > 0 for item in boundary_cases)
            / len(boundary_cases)
            if boundary_cases else 0.0
        ),
        "first_token_latency_ms": _mean(comparable, "first_token_latency_ms"),
        "total_latency_ms": _mean(comparable, "total_latency_ms"),
        "token_count": sum(max(0, int(item.get("token_count") or 0)) for item in comparable),
        "estimated_cost": round(
            sum(max(0.0, float(item.get("estimated_cost") or 0.0)) for item in comparable), 8
        ),
        "dimensions": dict(dimensions or {}),
    }
