from answer_builder import build_card_answer
from skills.base import DirectJSONSkill, SkillContext


class CardMetaSkill(DirectJSONSkill):
    name = "CardMetaSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "card_query" and (
            parsed.get("card_name") is not None
            or parsed.get("rank") is not None
            or parsed.get("top_n") is not None
        )

    def run(self, context: SkillContext) -> str:
        return build_card_answer(context.parsed, context.cards_meta_data)
