from skills.base import DirectJSONSkill, SkillContext
from structured_query import StructuredQueryError


class ExactDeckSkill(DirectJSONSkill):
    name = "ExactDeckSkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "deck_query" and len(parsed.get("deck_cards") or []) == 8

    def run(self, context: SkillContext) -> str:
        repository = context.structured_repository
        if repository is None:
            return "当前结构化统计仓库不可用，无法查询精确八卡卡组。"
        try:
            result = repository.deck_profile(context.parsed["deck_cards"])
        except StructuredQueryError as exc:
            return f"无法完成精确八卡查询：{exc.message}"
        deck = result["deck"]
        provenance = result["provenance"]
        return "\n".join(
            [
                "精确八卡卡组：" + " / ".join(deck["cards"]),
                f"- 对局：{deck['games']} 场",
                f"- 使用率：{deck['usage_rate']}%",
                f"- 净胜率：{deck['clean_win_rate']}%",
                f"- 胜/负/平：{deck['wins']} / {deck['losses']} / {deck['draws']}",
                "",
                "数据边界：按八张卡完全一致聚合，不区分塔楼、觉醒和精英形态。",
                "参考来源：",
                f"[1] {provenance.get('source')} | dataset_scope={provenance.get('dataset_scope')} "
                f"| unique_battles={provenance.get('unique_battles')}",
            ]
        )
