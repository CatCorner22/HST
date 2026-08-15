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
from gonzo.config import LONGFORM_MIN_WORDS, MODES
from gonzo.scoring.metrics import StyleReport, _words, score_text
from gonzo.style.spec import StyleSpec, load_spec
from gonzo.style.variance import VarianceDirector

# Tokens that must survive restyling: numbers, money, times, percentages.
# The trailing (?<![.,]) matters: without it the currency branch swallowed the
# sentence's closing punctuation, so "$4.3 million." in the source became the
# token "$4.3." and never matched the "$4.3" that did survive in the output --
# reporting a preserved figure as dropped.
_FACT = re.compile(
    r"[$£€]\s?\d[\d,.]*(?<![.,])"
    r"|\b\d{1,2}:\d{2}\b"
    r"|\b\d[\d,.]*(?<![.,])\s*%"
    r"|\b\d[\d,.]*(?<![.,])"
)


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
        # Grade and instruct on the same terms. The scorer treats anything over
        # LONGFORM_MIN_WORDS as long-form -- which mandates register motion and
        # an elegiac break -- so a long restyle drawn with longform=False was
        # being judged against requirements it was never given.
        longform = len(_words(source)) >= LONGFORM_MIN_WORDS
        directive = self._director.draw(seed=seed, basis=source, longform=longform)
        cfg = MODES["transfer"]

        prompt = (
            f"{directive.render()}\n\n"
            f"<length>{cfg.length_note}</length>\n\n"
            "<constraint>\n"
            "Every fact in the source must survive: names, numbers, dates, "
            "figures, quantities, and the argument being made. You may reorder, "
            "reframe, and re-voice freely. You may not add a fact the source "
            "does not contain, remove one it does, or change what it claims. If "
            "the source has few hard specifics, do not invent any about its "
            "subject — work with what is there.\n"
            "\n"
            "The narrator's frame is not a fact of the source. You are a "
            "correspondent who has read this material and is reporting it back: "
            "put yourself in the telling — where you are as you read it, what "
            "time it is, what it does to you — and react, digress, address the "
            "reader. Frame is voice; the source's claims are evidence. Never "
            "blur which is which: everything asserted about the subject stays "
            "exactly what the source asserts.\n"
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
