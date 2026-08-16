"""Critique: score any passage and explain what is off.

Runs the deterministic metrics and, optionally, the rubric judge. Useful on the
engine's own output and on the user's — this is how you find out whether a piece
is actually working or just loud.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from gonzo.client import GonzoClient, RefusalError, StructuredOutputError
from gonzo.scoring.judge import Judge
from gonzo.scoring.metrics import score_text
from gonzo.scoring.report import CombinedReport
from gonzo.style.spec import StyleSpec, load_spec

log = logging.getLogger(__name__)


@dataclass
class Critic:
    client: GonzoClient | None = None
    spec: StyleSpec = field(default_factory=load_spec)

    def critique(self, text: str, *, use_judge: bool = True, assignment: str | None = None) -> CombinedReport:
        metrics = score_text(text, self.spec)
        verdict = None
        if use_judge and self.client is not None:
            try:
                verdict = Judge(self.client).assess(text, assignment=assignment)
            except (StructuredOutputError, RefusalError) as exc:
                # Same policy as compose: the judge is an optional second
                # opinion, and a decline or malformed verdict must not throw
                # away the metrics already computed — a judge hiccup was
                # crashing `gonzo score` with a traceback and /api/score
                # with a bare 500.
                log.warning("judge unavailable, scoring on metrics alone: %s", exc)
        return CombinedReport(metrics=metrics, verdict=verdict)
