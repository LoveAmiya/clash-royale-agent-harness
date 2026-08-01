"""Agent 运行时的 Skill 发现与确定性选择。"""

from skills.base import Skill
from skills.base import SkillContext


class SkillRegistry:
    """将路由策略与单个 Skill 的实现分离。

    每个 Skill 声明 ``can_handle``；注册表按明确顺序评估。因此当意图重叠时，顺序很重要：
    具体业务 Skill 必须注册在宽泛的 RAG 证据兜底之前。
    """
    def __init__(self, skills: list[Skill] | None = None):
        self._skills = list(skills or [])

    def register(self, skill: Skill) -> None:
        self._skills.append(skill)

    def resolve(self, parsed: dict) -> Skill | None:
        """返回第一个兼容 Skill；没有匹配时返回 None 进入安全兜底路径。"""
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
    evidence_synthesis_builder=None,
) -> SkillRegistry:
    """按照“最具体业务 Skill 到 RAG 兜底”的顺序创建默认注册表。"""
    from skills.card_compare_skill import CardCompareSkill
    from skills.card_rank_lookup_skill import CardRankLookupSkill
    from skills.card_skill import CardMetaSkill
    from skills.deck_skill import DeckRankingSkill
    from skills.evidence_synthesis_skill import EvidenceSynthesisSkill
    from skills.rag_skill import RAGEvidenceSkill
    from skills.unsupported_clan_war_skill import UnsupportedClanWarSkill

    return SkillRegistry(
        [
            UnsupportedClanWarSkill(),
            DeckRankingSkill(),
            CardCompareSkill(),
            CardRankLookupSkill(),
            EvidenceSynthesisSkill(answer_builder=evidence_synthesis_builder),
            CardMetaSkill(),
            RAGEvidenceSkill(
                rag_answer_builder=rag_answer_builder,
                reviewer_model_builder=reviewer_model_builder,
            ),
        ]
    )
