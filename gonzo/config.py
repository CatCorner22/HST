"""Configuration: model selection, per-mode effort, generation targets."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
STYLE_DIR = PACKAGE_ROOT / "style"
WEB_DIR = REPO_ROOT / "web"

# Opus 5. Do not append a date suffix — the bare id is the complete model id.
MODEL = os.environ.get("GONZO_MODEL", "claude-opus-5")

# Cheaper model for the mechanical scoring pass, where judgment is narrow and
# the rubric does the work. Overridable for users who want one model everywhere.
JUDGE_MODEL = os.environ.get("GONZO_JUDGE_MODEL", "claude-sonnet-5")

# Server-side refusal fallback. Opus 5 ships elevated safety classifiers; a
# declined request returns HTTP 200 with stop_reason="refusal" rather than an
# error. "default" lets the API route by refusal category instead of pinning a
# model we would then have to maintain.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class ModeConfig:
    """Per-mode generation settings.

    `effort` trades thoroughness against latency and cost. `max_tokens` caps
    thinking *and* response text together on Opus 5, so it needs headroom above
    the target prose length.
    """

    effort: str
    max_tokens: int
    target_words: tuple[int, int] | None = None
    length_note: str = ""


# Opus 5 writes long by default and `effort` does not reliably shorten visible
# output — length is controlled by prompt, so each mode states its own target.
MODES: dict[str, ModeConfig] = {
    "chat": ModeConfig(
        effort="medium",
        max_tokens=8_000,
        target_words=(60, 220),
        length_note=(
            "This is conversation, not a filed piece. Answer in 60-220 words. "
            "One or two register bands is right at this length. Do not append a "
            "summary, and do not offer follow-up options."
        ),
    ),
    "compose": ModeConfig(
        effort="high",
        max_tokens=32_000,
        target_words=(700, 1200),
        length_note=(
            "This is a filed piece of 700-1200 words. Move through at least "
            "three register bands. The Elegiac break is mandatory."
        ),
    ),
    "transfer": ModeConfig(
        effort="medium",
        max_tokens=16_000,
        length_note=(
            "Restyle the supplied text. Every fact, figure, name, and claim in "
            "the source must survive intact and unaltered. Do not add facts. "
            "Roughly match the source length, within 50%."
        ),
    ),
    "judge": ModeConfig(effort="low", max_tokens=4_000),
}

# Scorer thresholds. A piece must clear these to pass. Tuned so that flat prose
# fails and all-tic pastiche fails — see tests/test_metrics.py.
THRESHOLDS: dict[str, float] = {
    "sentence_length_cv": 0.55,      # coefficient of variation; uniform prose ~0.3
    "fragment_rate": 0.04,           # share of sentences under 7 words
    "specificity_density": 3.0,      # hard specifics per 100 words
    "adjective_stack_rate": 0.15,    # stacks per 100 words
    "min_register_transitions": 2,   # long-form only
}

# Long-form pieces additionally require an Elegiac segment.
LONGFORM_MIN_WORDS = 400


def resolve_api_key() -> str | None:
    """Return an explicit API key if one is set.

    Returning None is not an error: the Anthropic SDK also resolves credentials
    from ANTHROPIC_AUTH_TOKEN or an `ant auth login` profile on disk, and a
    zero-arg client picks those up on its own.
    """
    return os.environ.get("ANTHROPIC_API_KEY")


@dataclass
class GenerationSettings:
    """Runtime knobs a caller may override per request."""

    mode: str = "chat"
    seed: int | None = None
    extra_directives: list[str] = field(default_factory=list)

    @property
    def config(self) -> ModeConfig:
        return MODES[self.mode]
