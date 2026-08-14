"""Scoring: deterministic stylometry plus an LLM rubric judge."""

from gonzo.scoring.metrics import StyleReport, score_text

__all__ = ["StyleReport", "score_text"]
