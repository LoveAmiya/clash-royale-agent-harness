"""Card-name normalization and alias resolution helpers."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Mapping, Sequence


def normalize_card_alias(text: str) -> str:
    """Make harmless English spelling differences resolve to one card alias."""
    normalized = re.sub(r"[._-]+", " ", text.strip().lower())
    return re.sub(r"\s+", " ", normalized)


class CardAliasResolver:
    """Build and query a stable alias catalog supplied by the parser facade."""

    def __init__(
        self,
        *,
        aliases: Mapping[str, Sequence[str]],
        overrides: Mapping[str, Sequence[str]],
        community_aliases: Mapping[str, Sequence[str]],
        editable_names: set[str],
        form_catalog: Sequence[str],
    ) -> None:
        self._aliases = aliases
        self._overrides = overrides
        self._community_aliases = community_aliases
        self._editable_names = editable_names
        self._form_catalog = tuple(form_catalog)

    @staticmethod
    def card_catalog_key(cards_meta_data: list[dict]) -> tuple[str, ...]:
        return tuple(str(item.get("card_name", "")).strip() for item in cards_meta_data)

    def build_card_aliases(self, cards_meta_data: list[dict]) -> dict[str, list[str]]:
        """Build complete aliases for the stable parser catalog plus snapshot cards."""
        return self.build_aliases(self.card_catalog_key(cards_meta_data))

    @lru_cache(maxsize=8)
    def build_aliases(self, snapshot_card_names: tuple[str, ...]) -> dict[str, list[str]]:
        """Cache the immutable alias catalog for the active card snapshot."""
        canonical_names = list(
            dict.fromkeys(
                [
                    *self._aliases,
                    *self._overrides,
                    *self._community_aliases,
                    *self._form_catalog,
                    *snapshot_card_names,
                ]
            )
        )
        aliases = {
            name: ([] if name in self._editable_names else list(self._aliases.get(name, [])))
            for name in canonical_names
            if name
        }
        for canonical, values in self._overrides.items():
            aliases.setdefault(canonical, []).extend(values)
        for canonical, values in self._community_aliases.items():
            if canonical in self._editable_names:
                continue
            aliases.setdefault(canonical, []).extend(values)
        for canonical in canonical_names:
            if not canonical:
                continue
            values = aliases.setdefault(canonical, [])
            values.extend([canonical.lower(), canonical.lower().replace(" ", "")])

            if canonical.endswith(" Evolution"):
                base = canonical.removesuffix(" Evolution")
                for base_alias in aliases.get(base, []):
                    values.extend(
                        [
                            f"{base_alias}\u8fdb\u5316",
                            f"\u8fdb\u5316{base_alias}",
                            f"\u89c9\u9192{base_alias}",
                            f"{base_alias}\u89c9\u9192",
                            f"evo {base_alias}",
                            f"evolved {base_alias}",
                        ]
                    )
                values.extend([f"{base.lower()} evolution", f"evo {base.lower()}"])
            elif canonical.startswith("Hero "):
                base = canonical.removeprefix("Hero ")
                for base_alias in aliases.get(base, []):
                    values.extend(
                        [
                            f"\u82f1\u96c4{base_alias}",
                            f"{base_alias}\u82f1\u96c4",
                            f"hero {base_alias}",
                        ]
                    )

            normalized_values = []
            for alias in values:
                if not alias.strip():
                    continue
                normalized = normalize_card_alias(alias)
                normalized_values.append(normalized)
                compact = normalized.replace(" ", "")
                if compact != normalized:
                    normalized_values.append(compact)
            aliases[canonical] = list(dict.fromkeys(normalized_values))
        return aliases

    @lru_cache(maxsize=8)
    def alias_patterns(
        self, snapshot_card_names: tuple[str, ...]
    ) -> tuple[tuple[str, re.Pattern], ...]:
        patterns: list[tuple[str, re.Pattern]] = []
        for card_name, aliases in self.build_aliases(snapshot_card_names).items():
            for alias in aliases:
                if not alias:
                    continue
                if re.fullmatch(r"[a-z0-9 .'-]+", alias):
                    expression = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
                else:
                    expression = re.escape(alias)
                patterns.append((card_name, re.compile(expression)))
        return tuple(patterns)

    def resolve_card_name(self, text: str, cards_meta_data: list[dict]) -> str | None:
        """Resolve the first distinct card mention in user order."""
        matches = self.resolve_card_names(text, cards_meta_data)
        return matches[0] if matches else None

    def resolve_card_names(self, text: str, cards_meta_data: list[dict]) -> list[str]:
        """Resolve distinct, non-overlapping card mentions in the user's order."""
        question = normalize_card_alias(text)
        matches: list[tuple[int, int, str]] = []
        for card_name, pattern in self.alias_patterns(self.card_catalog_key(cards_meta_data)):
            for match in pattern.finditer(question):
                matches.append((match.start(), match.end(), card_name))

        selected: list[tuple[int, int, str]] = []
        seen_cards: set[str] = set()
        for start, end, card_name in sorted(
            matches,
            key=lambda item: (item[0], -(item[1] - item[0]), item[2]),
        ):
            if card_name in seen_cards:
                continue
            if any(
                start < selected_end and end > selected_start
                for selected_start, selected_end, _ in selected
            ):
                continue
            selected.append((start, end, card_name))
            seen_cards.add(card_name)
        return [card_name for _, _, card_name in selected]


__all__ = ["CardAliasResolver", "normalize_card_alias"]
