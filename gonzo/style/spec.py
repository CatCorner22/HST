"""Loads the style specification and assembles the cached system prompt.

The assembled prefix must be **byte-stable across turns** — nothing dynamic may
enter it. A timestamp, a session id, or a per-request seed anywhere in this text
silently destroys prompt caching for every downstream token. `tests/test_spec.py`
guards this.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

import yaml

from gonzo.config import STYLE_DIR
from gonzo.persona import GUARDRAILS, persona_text


@dataclass(frozen=True)
class StyleSpec:
    """The loaded style system."""

    text: str
    bands: dict[str, Any]
    tics: dict[str, Any]
    imagery_domains: list[dict[str, str]]
    opening_moves: list[dict[str, str]]
    templates: list[dict[str, Any]]

    @property
    def band_names(self) -> list[str]:
        return list(self.bands)

    @property
    def tic_words(self) -> list[str]:
        return [w.lower() for w in self.tics["words"]]

    @property
    def tic_budget_per_500(self) -> int:
        return int(self.tics["budget_per_500_words"])

    def template(self, name: str) -> dict[str, Any]:
        for t in self.templates:
            if t["name"] == name:
                return t
        raise KeyError(f"unknown structural template: {name}")

    def system_prompt(self, wire: bool = False) -> str:
        """The stable, cacheable system prefix.

        Order matters: persona first (who), spec second (how), guardrails last
        (what overrides everything). Guardrails go last so they are the most
        recent instruction before the conversation begins.

        `wire` selects which capability section the persona carries and must
        mirror whether the request actually attaches the web search tool.
        Each variant is byte-stable, so wire-on and wire-off sessions each
        hold their own prompt cache; flipping mid-session costs one cache
        rewrite, nothing else.
        """
        return "\n\n---\n\n".join([persona_text(wire=wire), self.text, GUARDRAILS])


@functools.lru_cache(maxsize=1)
def load_spec() -> StyleSpec:
    """Load and cache the style system from disk."""
    text = (STYLE_DIR / "style_spec.md").read_text(encoding="utf-8")
    lexicon = yaml.safe_load((STYLE_DIR / "lexicon.yaml").read_text(encoding="utf-8"))
    structures = yaml.safe_load((STYLE_DIR / "structures.yaml").read_text(encoding="utf-8"))

    return StyleSpec(
        text=text,
        bands=lexicon["bands"],
        tics=lexicon["tics"],
        imagery_domains=lexicon["imagery_domains"],
        opening_moves=lexicon["opening_moves"],
        templates=structures["templates"],
    )
