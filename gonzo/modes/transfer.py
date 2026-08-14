"""Style transfer: restyle supplied text, preserve every fact.

The constraint that makes this useful rather than a toy: the source's facts,
figures, names, dates, and argument must survive intact. The engine changes how
it sounds, never what it says. A "restyling" that invents a statistic has
produced fiction, not prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gonzo.client import GonzoClient
from gonzo.config import MODES
from gonzo.scoring.metrics import StyleReport, score_text
from gonzo.style.spec import StyleSpec, load_spec
from gonzo.style.variance import VarianceDirector

# Tokens that must survive restyling: numbers, money, times, percentages.
_FACT = re.compile(r"[$£€]\s?\d[\d,.]*|\b\d{1,2}:\d{2}\b|\b\d[\d,.]*\s*%|\b\d[\d,.]*\b")


@dataclass
class TransferResult:
    text: str
    report: StyleReport
    dropped_facts: list[str] = field(default_factory=list)

    @property
    def facts_preserved(self) -> bool:
        return not self.dropped_facts


@dataclass
class Transferrer:
    client: GonzoClient
    spec: StyleSpec = field(default_factory=load_spec)

    def __post_init__(self) -> None:
        self._director = VarianceDirector(self.spec)

    def transfer(self, source: str, *, seed: int | None = None) -> TransferResult:
        directive = self._director.draw(seed=seed, basis=source, longform=False)
        cfg = MODES["transfer"]

        prompt = (
            f"{directive.render()}\n\n"
            f"<length>{cfg.length_note}</length>\n\n"
            "<constraint>\n"
            "Every fact in the source must survive: names, numbers, dates, "
            "figures, quantities, and the argument being made. You may reorder, "
            "reframe, and re-voice freely. You may not add a fact the source "
            "does not contain, remove one it does, or change what it claims. If "
            "the source has few hard specifics, do not invent any — work with "
            "what is there.\n"
            "</constraint>\n\n"
            "<source>\n"
            f"{source}\n"
            "</source>\n\n"
            "Rewrite the source in voice. Output the rewritten prose only."
        )

        text = self.client.complete(
            system_prompt=self.spec.system_prompt(),
            messages=[{"role": "user", "content": prompt}],
            mode="transfer",
        ).text

        return TransferResult(
            text=text,
            report=score_text(text, self.spec),
            dropped_facts=self._dropped(source, text),
        )

    @staticmethod
    def _dropped(source: str, output: str) -> list[str]:
        """Numeric facts present in the source but missing from the output.

        Deliberately narrow: numbers are checkable without a model in the loop,
        so this is a cheap deterministic guard rather than a full claim audit.

        It is conservative and will over-report. A source "12%" restyled to
        "twelve percent" is still flagged, because matching digits to spelled
        numerals reliably is a much larger problem than this check is worth.
        Treat a flag as "look at this", not as proof a fact was lost.
        """
        source_facts = {f.strip() for f in _FACT.findall(source)}
        output_facts = {f.strip() for f in _FACT.findall(output)}
        return sorted(source_facts - output_facts)
