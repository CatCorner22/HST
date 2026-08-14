"""Deterministic stylometry. No model calls, no third-party deps.

Measures the four things the research says actually distinguish this voice from
its imitations:

  1. rhythm dynamics  — sentence-length variance and fragment punctuation
  2. specificity      — hard anchoring detail per 100 words
  3. register motion  — how many bands the piece occupies, and whether the
                        elegiac break is present
  4. tic discipline   — signature vocabulary kept inside its budget

Fast and unit-testable, which matters: a scorer nobody can test is a scorer that
quietly always says yes. `tests/test_metrics.py` pins both directions — flat
corporate prose must score low, all-tic pastiche must fail the budget.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from gonzo.config import LONGFORM_MIN_WORDS, THRESHOLDS
from gonzo.style.spec import StyleSpec, load_spec

# --- tokenization ----------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])[\"')\]]*\s+|\n{2,}")
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")

# Hard specifics: the inventory detail that anchors hyperbole.
_NUMERAL = re.compile(r"\b\d[\d,.:]*\b")
_CURRENCY = re.compile(r"[$£€]\s?\d[\d,.]*")
_TIME = re.compile(r"\b\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?\b|\b\d{1,2}\s*[ap]\.?m\.?\b", re.I)
_PERCENT = re.compile(r"\b\d[\d,.]*\s*(?:%|percent)\b", re.I)
_MEASURE = re.compile(
    r"\b\d[\d,.]*\s*-?\s*(?:miles?|mph|feet|foot|ft|yards?|pounds?|lbs?|ounces?|oz|"
    r"gallons?|liters?|litres?|grams?|kilos?|kg|degrees?|acres?|rounds?|rooms?|"
    r"minutes?|hours?|days?|weeks?|months?|years?)\b",
    re.I,
)
# A proper noun that isn't just a sentence-initial capital.
_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][a-z]{2,}\b", re.M)

_EM_DASH = re.compile(r"—|--")
_ELLIPSIS = re.compile(r"\.\.\.|…")
_ALLCAPS = re.compile(r"\b[A-Z]{3,}\b")
_EXCLAIM = re.compile(r"!")

# Adjective detection without a POS tagger: morphology plus a closed list of
# common adjectives that don't carry a telltale suffix. Heuristic by design —
# it only needs to detect *stacking*, not to parse English.
_ADJ_SUFFIX = re.compile(
    r"(?:ous|ive|ful|less|able|ible|ical|istic|oid|ent|ant|ary|ish|like|"
    r"esque|ic|al|ed|ing|y)$",
    re.I,
)
_COMMON_ADJ = frozenset(
    """big small huge vast tiny great good bad worse worst best mad wild dark
    bright cheap rich poor hard soft loud quiet slow fast cold hot warm cool
    strange odd weird sick sweet sour raw rank foul clean dirty wet dry sharp
    dull blunt grim bleak stark bare thin thick tight loose free lost blind
    deaf numb dead live cheap mean lean flat round square long short high low
    old young new fresh stale rotten""".split()
)
# Words with adjective morphology that are almost never adjectives in practice.
_ADJ_STOP = frozenset(
    """the a an and or but if then than that this these those there here when
    where while very really only just also even still yet nearly almost
    something nothing anything everything morning evening during being having
    doing going nothing everybody somebody suddenly""".split()
)


def _sentences(text: str) -> list[str]:
    parts = (s.strip() for s in _SENTENCE_SPLIT.split(text))
    return [s for s in parts if s]


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _looks_adjectival(word: str) -> bool:
    low = word.lower()
    if low in _ADJ_STOP or len(low) < 4:
        return False
    return low in _COMMON_ADJ or bool(_ADJ_SUFFIX.search(low))


def _adjective_stacks(text: str, min_run: int = 3) -> int:
    """Count adjective stacks -- runs of >=min_run modifiers before a noun.

    Two patterns count, because morphology alone is not enough:

      A. Consecutive adjectival words: "vicious bloated atavistic swine".
      B. Comma-coordinate runs: "thick, sweet, ammoniac wall". The commas are
         the signal here -- English only coordinates like that between
         modifiers -- so a member need not look adjectival to count, as long as
         the run as a whole is majority-adjectival. This catches unusual
         coinages, which is exactly where this style lives.
    """
    stacks = 0
    for sentence in _sentences(text):
        tokens = re.findall(r"[A-Za-z][A-Za-z'’-]*|,", sentence)
        run: list[str] = []
        preceded_by_comma = False

        def flush(run: list[str]) -> int:
            if len(run) < min_run:
                return 0
            adjectival = sum(1 for w in run if _looks_adjectival(w))
            return 1 if adjectival >= max(2, (len(run) + 1) // 2) else 0

        for tok in tokens:
            if tok == ",":
                preceded_by_comma = True
                continue
            # A word joins the run if it looks adjectival, or if a comma just
            # coordinated it onto an existing run.
            if _looks_adjectival(tok) or (preceded_by_comma and run):
                run.append(tok)
            else:
                stacks += flush(run)
                run = []
            preceded_by_comma = False
        stacks += flush(run)
    return stacks


# --- register classification ----------------------------------------------


def _segment(text: str, target_sentences: int = 4) -> list[str]:
    """Split into scoring segments — paragraphs, or runs of sentences."""
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    segments: list[str] = []
    for para in paras:
        sents = _sentences(para)
        if len(sents) <= target_sentences:
            segments.append(para)
            continue
        for i in range(0, len(sents), target_sentences):
            segments.append(" ".join(sents[i : i + target_sentences]))
    return segments or ([text.strip()] if text.strip() else [])


def _cue_hits(low: str, cues: list[str]) -> int:
    """Count cue matches on word boundaries.

    Substring matching is wrong here and quietly poisons every band: "ended"
    fires on "attended", "lost" on "closest", "over" on "recovery".
    """
    total = 0
    for cue in cues:
        pattern = re.escape(cue)
        total += len(re.findall(rf"(?<!\w){pattern}(?!\w)", low))
    return total


def _distinct_cues(low: str, cues: list[str]) -> int:
    return sum(1 for cue in cues if re.search(rf"(?<!\w){re.escape(cue)}(?!\w)", low))


def _classify_band(segment: str, spec: StyleSpec) -> str:
    """Assign a register band by cue hits plus structural signals."""
    words = _words(segment)
    if not words:
        return "clinical"
    low = segment.lower()
    n = len(words)

    scores: dict[str, float] = {}
    for band, data in spec.bands.items():
        scores[band] = _cue_hits(low, data["cues"]) / max(n / 40, 1.0)

    sents = _sentences(segment) or [segment]
    lengths = [len(_words(s)) for s in sents]
    mean_len = statistics.fmean(lengths) if lengths else 0.0
    adj_rate = _adjective_stacks(segment) / max(n / 100, 0.5)
    specifics = _count_specifics(segment) / max(n / 100, 0.5)
    excite = len(_EXCLAIM.findall(segment)) + len(_ALLCAPS.findall(segment))

    # Structural signals. Weighted to roughly match cue magnitudes so neither
    # source dominates the other.
    scores["clinical"] += min(specifics / 4.0, 2.0)
    scores["clinical"] += 0.6 if adj_rate < 0.5 else 0.0
    scores["manic"] += 1.2 if mean_len > 26 else 0.0
    scores["manic"] += min(excite * 0.4, 1.2)
    scores["savage"] += min(adj_rate / 2.0, 1.5)
    # Elegiac: short plain sentences, no ornament, no shouting -- but plainness
    # alone is not mourning. Flat administrative prose is also short and plain,
    # so the structural bonus only applies when a genuine temporal-loss marker
    # is present, and a segment with no such marker can never be elegiac.
    # The elegiac passage in this style is plain, not vague -- it still carries
    # hard specifics, so it competes directly with clinical. Scale the bonus by
    # how many distinct loss markers are present so a genuine break outranks a
    # merely detailed paragraph, while one stray "once" does not.
    loss_markers = _distinct_cues(low, spec.bands["elegiac"]["cues"])
    if loss_markers and mean_len < 24 and adj_rate < 1.0 and excite == 0:
        scores["elegiac"] += 1.2 + min(loss_markers, 3) * 0.9
    if mean_len > 28 or excite:
        scores["elegiac"] -= 1.0
    if not loss_markers:
        scores["elegiac"] = float("-inf")

    return max(scores, key=lambda b: scores[b])


def _count_specifics(text: str) -> int:
    """Hard anchoring detail: numbers, money, times, measures, proper nouns."""
    return (
        len(_CURRENCY.findall(text))
        + len(_TIME.findall(text))
        + len(_PERCENT.findall(text))
        + len(_MEASURE.findall(text))
        + len(_NUMERAL.findall(text))
        + len(_PROPER.findall(text))
    )


# --- report ----------------------------------------------------------------


@dataclass
class StyleReport:
    """Measured style profile plus pass/fail diagnostics."""

    word_count: int
    sentence_count: int
    mean_sentence_length: float
    sentence_length_stdev: float
    sentence_length_cv: float
    fragment_rate: float
    longest_sentence: int
    specificity_density: float
    adjective_stack_rate: float
    em_dash_rate: float
    ellipsis_rate: float
    allcaps_rate: float
    exclamation_rate: float
    tic_count: int
    tic_rate_per_500: float
    tic_budget: float
    tic_violations: list[str]
    bands: list[str]
    band_shares: dict[str, float]
    register_transitions: int
    has_elegiac: bool
    is_longform: bool
    failures: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def passed(self) -> bool:
        return not self.failures

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self) | {"passed": self.passed}

    def summary(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [
            f"{verdict}  score {self.score:.1f}/100   ({self.word_count} words)",
            "",
            f"  rhythm       cv {self.sentence_length_cv:.2f} "
            f"(>= {THRESHOLDS['sentence_length_cv']})   "
            f"mean {self.mean_sentence_length:.1f}w   "
            f"fragments {self.fragment_rate:.0%}   longest {self.longest_sentence}w",
            f"  specificity  {self.specificity_density:.1f}/100w "
            f"(>= {THRESHOLDS['specificity_density']})",
            f"  adjectives   {self.adjective_stack_rate:.2f} stacks/100w "
            f"(>= {THRESHOLDS['adjective_stack_rate']})",
            f"  tics         {self.tic_count} used, {self.tic_rate_per_500:.1f}/500w "
            f"(budget {self.tic_budget:.1f})",
            f"  registers    {' -> '.join(self.bands) if self.bands else '(none)'}",
            f"               {self.register_transitions} transitions, "
            f"elegiac {'present' if self.has_elegiac else 'ABSENT'}",
        ]
        if self.failures:
            lines += ["", "  failures:"] + [f"    - {f}" for f in self.failures]
        return "\n".join(lines)


def score_text(text: str, spec: StyleSpec | None = None) -> StyleReport:
    """Measure a passage against the style specification."""
    spec = spec or load_spec()
    words = _words(text)
    sents = _sentences(text)
    n_words = len(words)
    n_sents = len(sents)

    if n_words == 0:
        return StyleReport(
            word_count=0, sentence_count=0, mean_sentence_length=0.0,
            sentence_length_stdev=0.0, sentence_length_cv=0.0, fragment_rate=0.0,
            longest_sentence=0, specificity_density=0.0, adjective_stack_rate=0.0,
            em_dash_rate=0.0, ellipsis_rate=0.0, allcaps_rate=0.0,
            exclamation_rate=0.0, tic_count=0, tic_rate_per_500=0.0, tic_budget=0.0,
            tic_violations=[], bands=[], band_shares={}, register_transitions=0,
            has_elegiac=False, is_longform=False, failures=["empty input"],
        )

    per_100 = n_words / 100
    lengths = [len(_words(s)) for s in sents] or [0]
    mean_len = statistics.fmean(lengths)
    stdev = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    cv = stdev / mean_len if mean_len else 0.0
    fragments = sum(1 for length in lengths if length < 7)

    # -- tics
    low = text.lower()
    tic_count = 0
    violations: list[str] = []
    for tic in spec.tic_words:
        pattern = re.compile(rf"\b{re.escape(tic)}\b" if " " not in tic else re.escape(tic))
        hits = len(pattern.findall(low))
        tic_count += hits
    for sentence in sents:
        s_low = sentence.lower()
        in_sentence = [
            tic for tic in spec.tic_words
            if re.search(rf"\b{re.escape(tic)}\b" if " " not in tic else re.escape(tic), s_low)
        ]
        if len(in_sentence) > int(spec.tics["max_per_sentence"]):
            violations.append(f"{len(in_sentence)} signature words in one sentence: {in_sentence}")

    tic_budget = spec.tic_budget_per_500 * max(n_words / 500, 0.2)
    tic_rate = tic_count / max(n_words / 500, 0.2)

    # -- registers
    segments = _segment(text)
    bands = [_classify_band(seg, spec) for seg in segments]
    collapsed = [b for i, b in enumerate(bands) if i == 0 or b != bands[i - 1]]
    transitions = max(len(collapsed) - 1, 0)
    shares = {b: bands.count(b) / len(bands) for b in set(bands)} if bands else {}

    report = StyleReport(
        word_count=n_words,
        sentence_count=n_sents,
        mean_sentence_length=mean_len,
        sentence_length_stdev=stdev,
        sentence_length_cv=cv,
        fragment_rate=fragments / n_sents if n_sents else 0.0,
        longest_sentence=max(lengths),
        specificity_density=_count_specifics(text) / per_100,
        adjective_stack_rate=_adjective_stacks(text) / per_100,
        em_dash_rate=len(_EM_DASH.findall(text)) / per_100,
        ellipsis_rate=len(_ELLIPSIS.findall(text)) / per_100,
        allcaps_rate=len(_ALLCAPS.findall(text)) / per_100,
        exclamation_rate=len(_EXCLAIM.findall(text)) / per_100,
        tic_count=tic_count,
        tic_rate_per_500=tic_rate,
        tic_budget=float(spec.tic_budget_per_500),
        tic_violations=violations,
        bands=collapsed,
        band_shares=shares,
        register_transitions=transitions,
        has_elegiac="elegiac" in bands,
        is_longform=n_words >= LONGFORM_MIN_WORDS,
    )
    _evaluate(report)
    return report


def _evaluate(r: StyleReport) -> None:
    """Apply thresholds, collect failures, compute the composite score."""
    t = THRESHOLDS

    if r.sentence_length_cv < t["sentence_length_cv"]:
        r.failures.append(
            f"uniform rhythm: sentence-length cv {r.sentence_length_cv:.2f} "
            f"< {t['sentence_length_cv']}. Break a long sentence, or add a fragment."
        )
    if r.fragment_rate < t["fragment_rate"]:
        r.failures.append(
            f"no fragment punctuation: {r.fragment_rate:.0%} of sentences are under "
            "7 words. Short sentences are the counterweight to the long runs."
        )
    if r.specificity_density < t["specificity_density"]:
        r.failures.append(
            f"unanchored: {r.specificity_density:.1f} hard specifics per 100 words "
            f"< {t['specificity_density']}. Hyperbole needs times, prices, names, "
            "quantities under it or it reads as noise."
        )
    if r.adjective_stack_rate < t["adjective_stack_rate"]:
        r.failures.append(
            f"no adjective stacking: {r.adjective_stack_rate:.2f} stacks/100 words. "
            "The three-adjective run is a signature move."
        )
    if r.tic_rate_per_500 > r.tic_budget:
        r.failures.append(
            f"pastiche: {r.tic_rate_per_500:.1f} signature words per 500 words, "
            f"budget is {r.tic_budget:.0f}. The style is not made of these words."
        )
    r.failures.extend(f"pastiche: {v}" for v in r.tic_violations)

    if r.is_longform:
        if r.register_transitions < t["min_register_transitions"]:
            r.failures.append(
                f"one-note: {r.register_transitions} register transitions in a "
                f"{r.word_count}-word piece. Long-form must move through at least "
                "three bands."
            )
        if not r.has_elegiac:
            r.failures.append(
                "no elegiac break: the sedate, unadorned passage is mandatory in "
                "long-form. It is the hardest band to write and the reason the "
                "rest of the piece means anything."
            )

    r.score = _composite(r)


def _composite(r: StyleReport) -> float:
    """0-100 composite. Each component saturates so no single axis dominates."""

    def ratio(value: float, target: float, cap: float = 1.0) -> float:
        return min(value / target, cap) if target else 0.0

    # Dynamic range: a fragment is only doing rhythmic work if there are long
    # accumulating sentences for it to interrupt. Uniformly short prose scores
    # a high fragment rate without having any dynamics at all.
    range_ratio = r.longest_sentence / r.mean_sentence_length if r.mean_sentence_length else 0.0
    has_long_runs = r.longest_sentence >= 25
    rhythm = (
        0.45 * ratio(r.sentence_length_cv, THRESHOLDS["sentence_length_cv"])
        + 0.30 * (ratio(r.fragment_rate, THRESHOLDS["fragment_rate"]) if has_long_runs else 0.0)
        + 0.25 * ratio(range_ratio, 2.5)
    )
    specificity = ratio(r.specificity_density, THRESHOLDS["specificity_density"])
    texture = ratio(r.adjective_stack_rate, THRESHOLDS["adjective_stack_rate"])

    if r.is_longform:
        motion = 0.6 * ratio(
            r.register_transitions, THRESHOLDS["min_register_transitions"]
        ) + 0.4 * (1.0 if r.has_elegiac else 0.0)
    else:
        motion = 1.0 if r.register_transitions >= 1 else 0.4

    # Tic discipline is a penalty, not a reward — using zero signature words is
    # perfectly fine, using too many is not.
    discipline = 1.0 if r.tic_rate_per_500 <= r.tic_budget else max(
        0.0, 1.0 - (r.tic_rate_per_500 - r.tic_budget) / max(r.tic_budget, 1.0)
    )
    if r.tic_violations:
        discipline *= 0.5

    weighted = (
        0.28 * rhythm
        + 0.26 * specificity
        + 0.14 * texture
        + 0.17 * motion
        + 0.15 * discipline
    )
    return round(weighted * 100, 1)
