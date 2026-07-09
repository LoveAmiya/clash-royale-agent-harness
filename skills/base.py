from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class SkillContext:
    user_text: str
    parsed: dict
    schedule_data: list[dict]
    top_decks_data: list[dict]
    cards_meta_data: list[dict]
    retriever: Any | None = None
    api_key: str = ""
    metadata: dict | None = None


class Skill(ABC):
    name = "Skill"

    @abstractmethod
    def can_handle(self, parsed: dict) -> bool:
        raise NotImplementedError

    @abstractmethod
    def run(self, context: SkillContext):
        raise NotImplementedError


class DirectJSONSkill(Skill):
    pass
