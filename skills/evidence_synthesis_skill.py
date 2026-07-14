"""处理需要多份证据和模型综合判断的环境、备战类问题。"""

from skills.base import Skill, SkillContext


class EvidenceSynthesisSkill(Skill):
    name = "EvidenceSynthesisSkill"

    def __init__(self, answer_builder=None):
        self._answer_builder = answer_builder

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") in {"meta_analysis_query", "match_preparation_query"}

    async def run(self, context: SkillContext) -> str:
        if not context.api_key:
            return "此类环境和备战问题需要模型综合本地证据，请先设置 OPENAI_API_KEY 后重试。"
        if self._answer_builder is None:
            raise RuntimeError("EvidenceSynthesisSkill is not configured")

        return await self._answer_builder(
            user_text=context.user_text,
            parsed=context.parsed,
            schedule_data=context.schedule_data,
            top_decks_data=context.top_decks_data,
            cards_meta_data=context.cards_meta_data,
            api_key=context.api_key,
        )
