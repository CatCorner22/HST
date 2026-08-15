"""Long-form composition with a score-then-revise loop.

Generation alone is a prompt. Generation, measurement, and targeted revision
against a spec is an engine — so a draft that misses the thresholds comes back
with its own diagnostics attached and gets rewritten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from gonzo.client import GonzoClient, RefusalError, StructuredOutputError
from gonzo.config import MODES
from gonzo.scoring.judge import Judge
from gonzo.scoring.metrics import score_text
from gonzo.scoring.report import CombinedReport
from gonzo.style.spec import StyleSpec, load_spec
from gonzo.style.variance import VarianceDirective, VarianceDirector

log = logging.getLogger(__name__)


@dataclass
class Draft:
    text: str
    report: CombinedReport
    revision: int
    directive: VarianceDirective


@dataclass
class Composer:
    """Produces a filed piece from an assignment."""

    client: GonzoClient
    spec: StyleSpec = field(default_factory=load_spec)
    use_judge: bool = True
    max_revisions: int = 2

    def __post_init__(self) -> None:
        self._director = VarianceDirector(self.spec)
        self._judge = Judge(self.client) if self.use_judge else None

    def compose(
        self,
        assignment: str,
        *,
        seed: int | None = None,
        revise: bool = True,
    ) -> Draft:
        """Write a piece, then revise it until it clears the spec or we run out
        of attempts. Returns the best draft seen, not merely the last."""
        directive = self._director.draw(seed=seed, basis=assignment, longform=True)
        cfg = MODES["compose"]

        prompt = (
            f"{directive.render()}\n\n"
            f"<length>{cfg.length_note}</length>\n\n"
            f"<assignment>\n{assignment}\n</assignment>\n\n"
            "File the piece. Output the prose only — no title, no notes about "
            "what you did, no closing summary, no commentary from outside the "
            "piece. In-world furniture is prose, not preamble: a dateline, a "
            "bracketed editor's note, a memo to the desk, a transcript scrap "
            "belong in the piece when it calls for them, unexplained. Commit "
            "completely — no hedged qualifiers, no balancing caveats, no "
            "apology for the register."
        )

        text = self.client.complete(
            system_prompt=self.spec.system_prompt(),
            messages=[{"role": "user", "content": prompt}],
            mode="compose",
        ).text

        best = Draft(text, self._assess(text, assignment), 0, directive)
        if not revise:
            return best

        for attempt in range(1, self.max_revisions + 1):
            if best.report.passed:
                break
            revised = self._revise(assignment, best, directive, prompt)
            candidate = Draft(revised, self._assess(revised, assignment), attempt, directive)
            # Keep the better draft — a revision can overcorrect, and shipping
            # a worse piece because it came later would be silly.
            if candidate.report.score > best.report.score or candidate.report.passed:
                best = candidate

        return best

    def _revise(
        self,
        assignment: str,
        draft: Draft,
        directive: VarianceDirective,
        original_prompt: str,
    ) -> str:
        prompt = (
            f"{original_prompt}\n\n"
            "<previous_draft>\n"
            f"{draft.text}\n"
            "</previous_draft>\n\n"
            "<critique>\n"
            "That draft was measured against the specification and fell short:\n\n"
            f"{draft.report.revision_brief()}\n"
            "</critique>\n\n"
            "Rewrite the piece, fixing every point above. Keep what worked — the "
            "specifics, the targets, the good lines. Do not simply pad it, and do "
            "not mention the critique. Output the prose only."
        )
        return self.client.complete(
            system_prompt=self.spec.system_prompt(),
            messages=[{"role": "user", "content": prompt}],
            mode="compose",
        ).text

    def _assess(self, text: str, assignment: str) -> CombinedReport:
        metrics = score_text(text, self.spec)
        verdict = None
        if self._judge is not None:
            try:
                verdict = self._judge.assess(text, assignment=assignment)
            except (StructuredOutputError, RefusalError) as exc:
                # The judge is a quality gate, not the deliverable. Losing a
                # finished draft because the grader misbehaved is the worst
                # possible trade -- degrade to metrics-only and say so.
                log.warning("judge unavailable, scoring on metrics alone: %s", exc)
        return CombinedReport(metrics=metrics, verdict=verdict)
