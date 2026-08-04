from query_parser import CARD_ALIAS_OVERRIDES
from skills.base import DirectJSONSkill, SkillContext
from structured_query import StructuredQueryError


def _display_name(card_name: str) -> str:
    return CARD_ALIAS_OVERRIDES.get(card_name, [card_name])[0]


class StructuredRelationshipSkill(DirectJSONSkill):
    name = "StructuredRelationshipSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "card_cooccurrence_query"

    def run(self, context: SkillContext) -> str:
        repository = context.structured_repository
        if repository is None:
            return "当前结构化统计仓库不可用，无法查询卡牌共现关系。"
        parsed = context.parsed
        provenance = None
        try:
            card_names = parsed.get("card_names") or []
            if len(card_names) == 2:
                result = repository.card_pair_stats(card_names)
                provenance = result["provenance"]
                first, second = (_display_name(name) for name in result["cards"])
                lines = [
                    f"{first}和{second}在同一方八卡卡组中共同出现 {result['games']} 次。",
                    f"- 共同出场样本净胜率：{result['clean_win_rate']}%",
                ]
            else:
                card_name = parsed.get("card_name")
                result = repository.card_teammate_rankings(
                    card_name,
                    top_n=int(parsed.get("top_n") or 10),
                )
                provenance = result["provenance"]
                lines = [f"最常与{result['display_name_zh']}同队出现的卡牌前 {len(result['teammates'])} 张："]
                for index, teammate in enumerate(result["teammates"], start=1):
                    lines.append(
                        f"{index}. {teammate['display_name_zh']}：共同出现 {teammate['games']} 次，"
                        f"共同出场样本净胜率 {teammate['clean_win_rate']}%"
                    )
        except StructuredQueryError as exc:
            return f"无法完成卡牌关系查询：{exc.message}"

        lines.extend(
            [
                "",
                "数据边界：共同出现按同一方的普通八卡组合统计，不代表完整配置形态或因果关系。",
                "参考来源：",
                f"[1] {provenance.get('source')} | dataset_scope={provenance.get('dataset_scope')} "
                f"| unique_battles={provenance.get('unique_battles')}",
            ]
        )
        return "\n".join(lines)
