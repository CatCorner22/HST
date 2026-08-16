"""The Variance Director — deterministic creative variance without `temperature`.

`temperature`, `top_p`, and `top_k` are rejected with a 400 on Claude Opus 5,
so there is no sampling knob to turn. Variance is engineered instead: each
request draws a seeded directive fixing the opening move, the register bands to
occupy, the imagery domain, and (for long-form) the structural template.

This is better than a temperature dial in three ways. It is reproducible — same
seed, same directive. It is inspectable — every generation logs what it was told
to do. And it varies the axes that actually matter for this voice, rather than
jittering token probabilities and hoping.

The rendered directive is appended to `messages`, never to `system`, so the
cached style-spec prefix stays byte-identical across every request.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass

from gonzo.style.spec import StyleSpec, load_spec


# Seeds are surfaced in the web UI and pasted back in to reproduce a run, so
# they must survive a JSON round-trip through JavaScript. JS numbers are IEEE
# doubles: anything above 2**53 is silently rounded, which meant a displayed
# 64-bit seed reproduced nothing.
_MAX_SEED = 2**53


@dataclass(frozen=True)
class VarianceDirective:
    """One request's stylistic assignment."""

    seed: int
    opening_move: str
    opening_brief: str
    dominant_band: str
    contrast_band: str
    imagery_domain: str
    imagery_brief: str
    template: str | None = None
    template_movements: tuple[str, ...] = ()
    # Long-form register contract (mandatory elegiac, full occupation of the
    # contrast band) — carried separately from `template` because transfer
    # needs the contract without the structure: compose templates mandate
    # invented scenes and companions, which a fact-preserving restyle is
    # forbidden to add.
    longform: bool = False

    def render(self) -> str:
        """Render as an instruction block for the message stream."""
        if self.longform:
            contrast_line = (
                f"REQUIRED CONTRAST REGISTER — {self.contrast_band} "
                "(you must genuinely occupy this band somewhere in the piece, not "
                "gesture at it)"
            )
        else:
            # Conversational length. Mandating full occupation of a second band
            # in 100 words forces a crammed register tour — the tell of an
            # imitation. A turn is enough.
            contrast_line = (
                f"CONTRAST REGISTER — {self.contrast_band}: turn toward this band "
                "once, even for a single sentence, if the reply runs long enough "
                "to earn it. Do not force a tour of every register."
            )

        lines = [
            "<style_directive>",
            "Stylistic assignment for this piece. Follow it. Never mention it, "
            "name it, or let its structure show as headings or labels.",
            "",
            f"OPENING MOVE — {self.opening_move}: {self.opening_brief}",
            f"DOMINANT REGISTER — {self.dominant_band}",
            contrast_line,
            f"IMAGERY DOMAIN — {self.imagery_domain}: {self.imagery_brief} "
            "(draw your figurative language primarily from here)",
        ]

        if self.template is None:
            lines += [
                "PRECEDENCE — this assignment serves the spec, never overrides it. "
                "If the subject in front of you cannot honestly carry the drawn "
                "registers or imagery — grief, love, a small kindness, an occasion "
                "of the reader's own — shift to the register the subject demands "
                "(elegiac may lead here) and let the imagery domain go quiet. "
                "Never install a villain to make the assignment fit."
            ]

        if self.template:
            lines += ["", f"STRUCTURE — {self.template}. Move through these beats in order:"]
            lines += [f"  {i}. {m}" for i, m in enumerate(self.template_movements, 1)]
            lines += [
                "Execute the opening move as your entry into beat 1 — they "
                "describe the same paragraphs, not two openings.",
                "If the assignment contains no accused, no fraud, and no "
                "proceeding, do not invent one — retarget this structure's "
                "adversarial beats at mediocrity, euphemism, the sales pitch, "
                "or the industry around the thing, or fold them into the "
                "elegiac undertow. The spec's 'Never install a villain' "
                "outranks every beat above.",
            ]

        lines += ["</style_directive>"]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return asdict(self)


class VarianceDirector:
    """Draws seeded stylistic directives."""

    def __init__(self, spec: StyleSpec | None = None) -> None:
        self.spec = spec or load_spec()

    @staticmethod
    def seed_from(text: str) -> int:
        """Derive a stable seed from arbitrary text.

        Used when no explicit seed is given, so repeating a prompt reproduces
        its output while different prompts diverge.
        """
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % _MAX_SEED

    def draw(
        self,
        *,
        seed: int | None = None,
        basis: str = "",
        longform: bool = False,
        structure: bool = True,
    ) -> VarianceDirective:
        """Draw a directive.

        `seed` wins if given; otherwise the seed is derived from `basis` so the
        same assignment reproduces. Passing neither draws at random.

        `structure=False` grants the long-form register contract (mandatory
        elegiac, genuine occupation) without a structural template. Transfer
        needs this: compose templates mandate invented scenes, dialogue, and
        companions — beats that directly contradict a restyle's "add no fact"
        constraint, and one draw in ten was injecting the companion the
        persona explicitly bans from restyling work.
        """
        if seed is None:
            seed = self.seed_from(basis) if basis else random.getrandbits(53)

        rng = random.Random(seed)

        move = rng.choice(self.spec.opening_moves)
        domain = rng.choice(self.spec.imagery_domains)

        # The Elegiac band is the hardest to write and the most-skipped feature
        # of the style, so it is never the dominant band — it earns its force by
        # arriving as contrast. Long-form always mandates it, because a filed
        # piece without the break reads as pastiche.
        candidates = [b for b in self.spec.band_names if b != "elegiac"]
        dominant = rng.choice(candidates)

        if longform:
            contrast = "elegiac"
        else:
            contrast = rng.choice([b for b in self.spec.band_names if b != dominant])

        template_name: str | None = None
        movements: tuple[str, ...] = ()
        if longform and structure:
            template = rng.choice(self.spec.templates)
            template_name = template["name"]
            movements = tuple(template["movements"])
            # The opening move and the template's first movement both govern the
            # opening paragraphs. Drawing them independently produced a directive
            # at war with itself in ~45% of long-form draws (90/200 seeded — e.g.
            # seed 11: "open on weather" vs. eulogy's "state what ended, flat") —
            # and a model given two contradictory openings writes a muddled
            # compromise. Redraw from the template's compatible pool so the
            # assignment is coherent by construction.
            allowed = template.get("compatible_openings")
            if allowed:
                pool = [m for m in self.spec.opening_moves if m["name"] in allowed]
                if pool:
                    move = rng.choice(pool)

        return VarianceDirective(
            seed=seed,
            opening_move=move["name"],
            opening_brief=move["brief"],
            dominant_band=dominant,
            contrast_band=contrast,
            imagery_domain=domain["name"],
            imagery_brief=domain["brief"],
            template=template_name,
            template_movements=movements,
            longform=longform,
        )
