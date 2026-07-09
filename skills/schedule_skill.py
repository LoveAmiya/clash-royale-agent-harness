from answer_builder import build_schedule_answer
from skills.base import DirectJSONSkill, SkillContext


class ScheduleQuerySkill(DirectJSONSkill):
    name = "ScheduleQuerySkill"

    def can_handle(self, parsed: dict) -> bool:
        return parsed.get("intent") == "schedule_query"

    def run(self, context: SkillContext) -> str:
        return build_schedule_answer(context.parsed, context.schedule_data)
