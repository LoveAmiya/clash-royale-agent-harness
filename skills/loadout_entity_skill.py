"""Direct structured answers for towers and explicit card forms."""

from skills.base import DirectJSONSkill, SkillContext
from structured_query import StructuredQueryError


class LoadoutEntitySkill(DirectJSONSkill):
    name = "LoadoutEntitySkill"

    def can_handle(self, parsed: dict) -> bool:
        return (
            parsed.get("intent") == "card_query"
            and parsed.get("entity_mode") == "loadout_entity"
            and parsed.get("entity_name") is not None
            and parsed.get("special_state") in {"tower", "evolution", "elite", "ordinary"}
        )

    def run(self, context: SkillContext) -> str:
        repository = context.structured_repository
        if repository is None:
            return "当前数据范围没有可用的完整配置结构化索引。"
        parsed = context.parsed
        try:
            payload = repository.entity_stats_by_reference(
                parsed.get("entity_type"),
                parsed.get("entity_name"),
                parsed.get("special_state"),
            )
        except StructuredQueryError as exc:
            return f"当前数据范围没有该完整配置实体的结构化证据：{exc.message}"

        entity = payload["entity"]
        requested_metrics = list(parsed.get("metrics") or [])
        if not requested_metrics and parsed.get("metric"):
            requested_metrics = [parsed["metric"]]
        metric_fields = {
            "usage_rate": ("使用率", "usage_rate"),
            "win_rate": ("胜率", "clean_win_rate"),
            "clean_win_rate": ("干净胜率", "clean_win_rate"),
        }
        lines = [f"{entity['display_name_zh']} 的完整配置实体指标："]
        for metric in requested_metrics:
            if metric in metric_fields:
                label, field = metric_fields[metric]
                lines.append(f"- {label}：{entity[field]}%")
        lines.append(f"- 样本出场：{entity['appearances']} 次")

        provenance = payload.get("provenance") or {}
        lines.extend(
            [
                "",
                "数据边界：该统计严格区分塔楼、觉醒、精英和普通形态，"
                "不会退回普通八卡口径。",
                "参考来源：",
                f"[1] {provenance.get('source', '结构化索引')}"
                f" | dataset_scope={provenance.get('dataset_scope', '当前范围')}"
                f" | unique_battles={provenance.get('unique_battles', '未知')}",
            ]
        )
        return "\n".join(lines)
