from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SkillContext:
    user_text: str
    parsed: dict
    schedule_data: list[dict]
    top_decks_data: list[dict]
    cards_meta_data: list[dict]
    card_deck_stats: dict[str, list[dict]] = field(default_factory=dict)
    structured_repository: Any | None = None
    retriever: Any | None = None
    api_key: str = ""
    metadata: dict | None = None
    event_sink: Any | None = None
    stream_content: bool = True


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
