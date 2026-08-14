"""Critique: score any passage and explain what is off.

Runs the deterministic metrics and, optionally, the rubric judge. Useful on the
engine's own output and on the user's — this is how you find out whether a piece
is actually working or just loud.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gonzo.client import GonzoClient
from gonzo.scoring.judge import Judge
from gonzo.scoring.metrics import score_text
from gonzo.scoring.report import CombinedReport
from gonzo.style.spec import StyleSpec, load_spec


@dataclass
class Critic:
    client: GonzoClient | None = None
    spec: StyleSpec = field(default_factory=load_spec)

    def critique(self, text: str, *, use_judge: bool = True, assignment: str | None = None) -> CombinedReport:
        metrics = score_text(text, self.spec)
        verdict = None
        if use_judge and self.client is not None:
            verdict = Judge(self.client).assess(text, assignment=assignment)
        return CombinedReport(metrics=metrics, verdict=verdict)
