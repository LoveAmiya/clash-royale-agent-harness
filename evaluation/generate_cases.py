"""Generate the reviewed, deterministic evaluation corpus.

This script is intentionally not called by the test runner.  It is a controlled
maintenance tool: review the resulting JSONL diff before accepting a refreshed
snapshot or adding a new intent.  Runtime evaluation always consumes the static
``evaluation/cases.jsonl`` committed alongside the code.
"""

import json
from pathlib import Path

from evaluation.contract_fixtures import sample_cards


ROOT = Path(__file__).resolve().parent.parent
CASES_FILE = ROOT / "evaluation" / "cases.jsonl"


def card_case(case_id: str, category: str, question: str, card_name: str, metric: str, *, metrics=None) -> dict:
    expected_fields = {"card_name": card_name, "metric": metric}
    if metrics is not None:
        expected_fields["metrics"] = metrics
    return {
        "id": case_id,
        "category": category,
        "question": question,
        "expected_intent": "card_query",
        "expected_skill": "CardMetaSkill",
        "expected_fields": expected_fields,
        "answer_contains": [card_name],
    }


def build_cases(cards: list[dict]) -> list[dict]:
    cases: list[dict] = []
    names = [item["card_name"] for item in cards]

    for index, card_name in enumerate(names, start=1):
        cases.append(card_case(
            f"card-usage-{index:03d}",
            "card_metric_english",
            f"{card_name} usage rate",
            card_name,
            "usage_rate",
            metrics=["usage_rate"],
        ))
        cases.append(card_case(
            f"card-win-{index:03d}",
            "card_metric_english",
            f"{card_name} win rate",
            card_name,
            "win_rate",
            metrics=["win_rate"],
        ))

    for index, card_name in enumerate(names[:24], start=1):
        cases.append(card_case(
            f"card-dual-metric-{index:03d}",
            "card_metric_multiple",
            f"{card_name} usage rate and win rate",
            card_name,
            "usage_rate",
            metrics=["usage_rate", "win_rate"],
        ))

    aliases = [
        ("Fireball", "\u706b\u7403"),
        ("Poison", "\u6bd2\u836f"),
        ("Electro Giant", "\u96f7\u7535\u5de8\u4eba"),
        ("Hog Rider", "\u91ce\u732a"),
        ("The Log", "\u6eda\u6728"),
        ("Miner", "\u77ff\u5de5"),
        ("Baby Dragon", "\u98de\u9f99\u5b9d\u5b9d"),
        ("Electro Wizard", "\u7535\u6cd5"),
        ("P.E.K.K.A", "\u5927\u76ae\u5361"),
        ("Royal Giant", "\u7687\u5de8"),
        ("Goblin Barrel", "\u98de\u6876"),
        ("Graveyard", "\u5893\u56ed"),
    ]
    for index, (card_name, alias) in enumerate(aliases, start=1):
        cases.append(card_case(
            f"card-alias-{index:03d}",
            "card_alias_chinese",
            f"{alias}\u4f7f\u7528\u7387",
            card_name,
            "usage_rate",
            metrics=["usage_rate"],
        ))

    pairs = [
        ("Fireball", "Poison"),
        ("Skeletons", "Barbarian Barrel"),
        ("The Log", "Tornado"),
        ("Hog Rider", "Miner"),
        ("Royal Giant", "Electro Giant"),
        ("Baby Dragon", "Inferno Dragon"),
        ("Knight", "Valkyrie"),
        ("Cannon", "Tesla"),
        ("Goblin Barrel", "Graveyard"),
        ("Balloon", "Lava Hound"),
    ]
    for index, (first, second) in enumerate(pairs, start=1):
        cases.append({
            "id": f"card-compare-{index:03d}",
            "category": "card_comparison_english",
            "question": f"{first} vs {second} win rate",
            "expected_intent": "card_compare_query",
            "expected_skill": "CardCompareSkill",
            "expected_fields": {
                "card_names": [first, second],
                "compare_metric": "win_rate",
            },
            "answer_contains": [first, second],
        })

    for index, card_name in enumerate(names[:10], start=1):
        cases.append({
            "id": f"card-rank-{index:03d}",
            "category": "card_rank_lookup_english",
            "question": f"What rank position is {card_name} by usage rate?",
            "expected_intent": "card_rank_lookup_query",
            "expected_skill": "CardRankLookupSkill",
            "expected_fields": {"card_name": card_name, "metric": "usage_rate"},
            "answer_contains": [card_name],
        })

    for top_n in range(1, 16):
        cases.append({
            "id": f"deck-ranking-{top_n:03d}",
            "category": "deck_ranking_english",
            "question": f"top {top_n} deck ranking",
            "expected_intent": "deck_query",
            "expected_skill": "DeckRankingSkill",
            "expected_fields": {"top_n": top_n, "metric": "usage_rate"},
            "answer_contains": [],
        })

    for round_number in range(1, 12):
        cases.append({
            "id": f"schedule-round-{round_number:03d}",
            "category": "removed_clan_war_feature",
            "question": f"round {round_number} match schedule",
            "expected_intent": "schedule_query",
            "expected_skill": "UnsupportedClanWarSkill",
            "expected_fields": {"round": round_number},
            "answer_contains": ["已从本项目移除"],
        })

    for index, question in enumerate([
        "what is the weather forecast",
        "write a recipe for soup",
        "what is the capital of France",
        "tell me a joke about programming",
        "translate this paragraph into Spanish",
        "who won the last NBA game",
        "plan a trip to Tokyo",
        "summarize a chemistry paper",
        "how do I repair a bicycle",
        "what is the price of bitcoin",
        "compose a haiku about rain",
        "explain quantum entanglement",
    ], start=1):
        cases.append({
            "id": f"reject-out-of-domain-{index:03d}",
            "category": "out_of_domain_rejection",
            "question": question,
            "expected_intent": "reject",
            "expected_skill": None,
            "expected_fields": {},
            "answer_contains": [],
        })

    for index, question in enumerate([
        "current meta analysis",
        "current environment analysis",
        "current meta decks analysis",
        "mainstream decks in the current meta",
    ], start=1):
        cases.append({
            "id": f"rag-route-{index:03d}",
            "category": "rag_route_optional",
            "question": question,
            "expected_intent": "meta_analysis_query",
            "expected_skill": "EvidenceSynthesisSkill",
            "expected_fields": {},
            "answer_contains": [],
            "optional": True,
        })

    multi_cases = [
        (
            "Fireball and Poison usage rates",
            [
                {"intent": "card_query", "card_name": "Fireball", "metrics": ["usage_rate"]},
                {"intent": "card_query", "card_name": "Poison", "metrics": ["usage_rate"]},
            ],
        ),
        (
            "Fireball and Poison win rates",
            [
                {"intent": "card_query", "card_name": "Fireball", "metrics": ["win_rate"]},
                {"intent": "card_query", "card_name": "Poison", "metrics": ["win_rate"]},
            ],
        ),
        (
            "Skeletons and The Log usage rates",
            [
                {"intent": "card_query", "card_name": "Skeletons", "metrics": ["usage_rate"]},
                {"intent": "card_query", "card_name": "The Log", "metrics": ["usage_rate"]},
            ],
        ),
        (
            "Electro Giant usage rate and win rate, plus current meta analysis",
            [
                {"intent": "card_query", "card_name": "Electro Giant", "metrics": ["usage_rate", "win_rate"]},
                {"intent": "meta_analysis_query", "card_name": None, "metrics": None},
            ],
        ),
        (
            "Hog Rider usage rate and win rate, plus current meta analysis",
            [
                {"intent": "card_query", "card_name": "Hog Rider", "metrics": ["usage_rate", "win_rate"]},
                {"intent": "meta_analysis_query", "card_name": None, "metrics": None},
            ],
        ),
        (
            "Royal Giant usage rate and win rate, plus current meta analysis",
            [
                {"intent": "card_query", "card_name": "Royal Giant", "metrics": ["usage_rate", "win_rate"]},
                {"intent": "meta_analysis_query", "card_name": None, "metrics": None},
            ],
        ),
    ]
    for index, (question, expected_subqueries) in enumerate(multi_cases, start=1):
        cases.append({
            "id": f"multi-intent-{index:03d}",
            "category": "multi_intent_decomposition",
            "question": question,
            "expected_intent": "multi_intent",
            "expected_skill": None,
            "expected_fields": {},
            "answer_contains": [],
            "expected_subqueries": expected_subqueries,
        })

    return cases


def main() -> int:
    cards = sample_cards()
    cases = build_cases(cards)
    ids = [case["id"] for case in cases]
    if len(cases) < 300:
        raise ValueError(f"expected at least 300 cases, generated {len(cases)}")
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    with CASES_FILE.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(json.dumps(case, ensure_ascii=False, sort_keys=True) for case in cases) + "\n")
    print(f"Wrote {len(cases)} reviewed static cases to {CASES_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
