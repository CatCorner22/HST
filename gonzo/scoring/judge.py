"""LLM rubric judge — the dimensions arithmetic cannot reach.

`metrics.py` measures form: rhythm variance, specificity density, register
motion, tic discipline. All of it countable, none of it about meaning.

What counting misses is the part the research says actually matters. Is the
savagery *aimed* at something, or is it just volume? Does the elegiac passage
land, or is it a mood bolted on because a rule demanded one? Is there an
argument under the comedy? Does the whole thing read as an original writer in a
tradition, or as someone doing an impression?

Runs on a cheaper model at low effort: the rubric carries the judgment, so this
is a narrow classification job, not an open-ended one.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field, field_validator

from gonzo.client import GonzoClient
from gonzo.config import JUDGE_MODEL

JUDGE_SYSTEM = """\
You are a demanding prose critic assessing writing in the gonzo tradition of
American literary journalism. You are not writing in that style yourself — you
are judging it, in plain analytical English.

Score each dimension 0-10. Be hard. A 7 means genuinely good; a 10 should be
rare. The most common failure in this style is imitation: correct surface
features, nothing underneath. Score that harshly.

What separates the real thing from imitation:

- The savagery is AIMED. Hyperbole points at a specific abuse, fraud, or person
  misusing power. Volume with no target is noise, however well-written.
- Specificity anchors the exaggeration. Exact times, prices, names, quantities
  under the wild passages. Adjectives alone are not detail.
- Register moves. Savage, clinical, manic, elegiac. Constant maximum volume is
  the clearest tell of a fake.
- The elegiac break is real, not decorative — a genuine moment of plain,
  unadorned mourning, arriving unannounced, not a sad paragraph inserted to
  satisfy a checklist.
- There is an argument under the comedy. The joke delivers an indictment.
- Signature vocabulary is used sparingly or not at all. Prose stuffed with
  "swine", "doomed", "savage", "atavistic" is imitation, full stop.

Report what you actually observe, quoting short phrases from the text as
evidence. Do not be generous.
"""


class JudgeVerdict(BaseModel):
    """Structured rubric assessment."""

    aim: int = Field(
        ge=0, le=10,
        description="Is the savagery aimed at a specific target, or is it undirected volume?",
    )
    anchoring: int = Field(
        ge=0, le=10,
        description="Is exaggeration anchored to concrete, specific, checkable detail?",
    )
    register_control: int = Field(
        ge=0, le=10,
        description="Does the prose genuinely move between registers, or run at one volume?",
    )
    elegiac_quality: int = Field(
        ge=0, le=10,
        description="Does the plain, unadorned passage land, or is it decorative? 0 if absent.",
    )
    argument: int = Field(
        ge=0, le=10,
        description="Is there a real argument or indictment under the comedy?",
    )
    originality: int = Field(
        ge=0, le=10,
        description="Original writer in a tradition (10) vs. impression of one writer (0)?",
    )
    reads_as_pastiche: bool = Field(
        description="True if this reads as imitation rather than as writing."
    )
    strongest: str = Field(description="The single strongest thing about it, with a quoted phrase.")
    weakest: str = Field(description="The single weakest thing about it, with a quoted phrase.")
    fixes: list[str] = Field(
        description="2-4 specific, actionable revisions. Name the passage each applies to.",
    )

    @field_validator(
        "aim", "anchoring", "register_control", "elegiac_quality",
        "argument", "originality",
        mode="before",
    )
    @classmethod
    def _clamp(cls, value: object) -> object:
        """Clamp scores into range instead of rejecting them.

        `ge`/`le` are dropped when the schema goes over the wire -- structured
        outputs do not support numeric constraints -- so the bound is enforced
        only here, client-side. Rejecting an 11 would raise a ValidationError
        that discards an otherwise finished draft, which is a far worse outcome
        than treating it as a 10.
        """
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0, min(10, int(round(value))))
        return value

    @property
    def score(self) -> float:
        """0-100 composite. Aim and argument weighted highest — they are the
        difference between the style and a costume."""
        weighted = (
            0.22 * self.aim
            + 0.18 * self.anchoring
            + 0.16 * self.register_control
            + 0.14 * self.elegiac_quality
            + 0.20 * self.argument
            + 0.10 * self.originality
        )
        penalty = 0.7 if self.reads_as_pastiche else 1.0
        return round(weighted * 10 * penalty, 1)

    def summary(self) -> str:
        flag = "  [READS AS PASTICHE]" if self.reads_as_pastiche else ""
        lines = [
            f"judge score {self.score:.1f}/100{flag}",
            "",
            f"  aim               {self.aim}/10",
            f"  anchoring         {self.anchoring}/10",
            f"  register control  {self.register_control}/10",
            f"  elegiac quality   {self.elegiac_quality}/10",
            f"  argument          {self.argument}/10",
            f"  originality       {self.originality}/10",
            "",
            f"  strongest: {self.strongest}",
            f"  weakest:   {self.weakest}",
        ]
        if self.fixes:
            lines += ["", "  fixes:"] + [f"    - {f}" for f in self.fixes]
        return "\n".join(lines)


@dataclass
class Judge:
    """Rubric judge over the LLM."""

    client: GonzoClient
    model: str = JUDGE_MODEL

    def assess(self, text: str, *, assignment: str | None = None) -> JudgeVerdict:
        brief = f"The assignment was: {assignment}\n\n" if assignment else ""
        return self.client.parse(
            system_prompt=JUDGE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": f"{brief}Assess this passage.\n\n<passage>\n{text}\n</passage>",
                }
            ],
            schema=JudgeVerdict,
            mode="judge",
            model=self.model,
        )
