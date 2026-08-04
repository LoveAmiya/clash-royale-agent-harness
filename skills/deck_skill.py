from answer_builder import build_deck_answer
from skills.base import DirectJSONSkill, SkillContext


class DeckRankingSkill(DirectJSONSkill):
    name = "DeckRankingSkill"

    def can_handle(self, parsed: dict) -> bool:
        return (
            parsed.get("intent") == "deck_query"
            and not parsed.get("deck_cards")
            and (
                parsed.get("card_name") is not None
                or parsed.get("rank") is not None
                or parsed.get("top_n") is not None
            )
        )

    def run(self, context: SkillContext) -> str:
        return build_deck_answer(context.parsed, context.top_decks_data, context.card_deck_stats)
