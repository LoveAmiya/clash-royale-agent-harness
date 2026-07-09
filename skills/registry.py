from skills.base import Skill
from skills.base import SkillContext


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills = list(skills or [])

    def register(self, skill: Skill) -> None:
        self._skills.append(skill)

    def resolve(self, parsed: dict) -> Skill | None:
        for skill in self._skills:
            if skill.can_handle(parsed):
                return skill
        return None

    def select(self, context: SkillContext) -> Skill | None:
        return self.resolve(context.parsed)


def build_default_registry(
    *,
    rag_answer_builder=None,
    reviewer_model_builder=None,
) -> SkillRegistry:
    from skills.card_compare_skill import CardCompareSkill
    from skills.card_rank_lookup_skill import CardRankLookupSkill
    from skills.card_skill import CardMetaSkill
    from skills.deck_skill import DeckRankingSkill
    from skills.match_preparation_skill import MatchPreparationSkill
    from skills.rag_skill import RAGEvidenceSkill
    from skills.schedule_skill import ScheduleQuerySkill
    from skills.schedule_summary_skill import ScheduleSummarySkill

    return SkillRegistry(
        [
            ScheduleSummarySkill(),
            ScheduleQuerySkill(),
            DeckRankingSkill(),
            CardCompareSkill(),
            CardRankLookupSkill(),
            MatchPreparationSkill(),
            CardMetaSkill(),
            RAGEvidenceSkill(
                rag_answer_builder=rag_answer_builder,
                reviewer_model_builder=reviewer_model_builder,
            ),
        ]
    )
