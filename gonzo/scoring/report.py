"""Combined report: deterministic metrics plus optional judge verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gonzo.scoring.judge import JudgeVerdict
from gonzo.scoring.metrics import StyleReport


@dataclass
class CombinedReport:
    metrics: StyleReport
    verdict: JudgeVerdict | None = None

    @property
    def passed(self) -> bool:
        """Both gates must clear. The metrics catch form, the judge catches
        the costume problem, and a piece can fail either independently."""
        if not self.metrics.passed:
            return False
        if self.verdict is None:
            return True
        return not self.verdict.reads_as_pastiche and self.verdict.score >= 60

    @property
    def score(self) -> float:
        if self.verdict is None:
            return self.metrics.score
        # Judge weighted higher: form is necessary, meaning is decisive.
        return round(0.4 * self.metrics.score + 0.6 * self.verdict.score, 1)

    def revision_brief(self) -> str:
        """Actionable notes for a revision pass."""
        notes = list(self.metrics.failures)
        if self.verdict:
            notes.extend(self.verdict.fixes)
            if self.verdict.reads_as_pastiche:
                notes.insert(0, "This reads as imitation rather than as writing. Rewrite, don't patch.")
        return "\n".join(f"- {n}" for n in notes)

    def summary(self) -> str:
        parts = [self.metrics.summary()]
        if self.verdict:
            parts += ["", self.verdict.summary()]
            parts += ["", f"COMBINED {self.score:.1f}/100  {'PASS' if self.passed else 'FAIL'}"]
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics.to_dict(),
            "verdict": self.verdict.model_dump() if self.verdict else None,
            "score": self.score,
            "passed": self.passed,
        }
