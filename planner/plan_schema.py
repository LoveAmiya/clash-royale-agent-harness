from dataclasses import asdict, dataclass


@dataclass(slots=True)
class PlanStep:
    step_id: str
    skill_name: str
    description: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class Plan:
    plan_type: str
    trigger_intent: str
    steps: list[PlanStep]

    def to_dict(self) -> dict:
        return {
            "plan_type": self.plan_type,
            "trigger_intent": self.trigger_intent,
            "steps": [step.to_dict() for step in self.steps],
        }
