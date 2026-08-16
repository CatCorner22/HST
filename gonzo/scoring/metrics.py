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

# Abbreviations that end in a period without ending a sentence. Splitting
# naively on "." made "Mr. Smith" two sentences and "$2.1 million" a sentence
# boundary mid-number, which inflated sentence_count and inverted every rhythm
# metric derived from it.
# Titles and the like: these are never the last word of a sentence, so their
# period is always protected. Entries here must not be ordinary English words
# — a homograph ("no", "sun") protected unconditionally merges real sentence
# boundaries ("The answer was no. Nobody argued." became one sentence),
# deflating fragment counts and manufacturing false tic-adjacency violations.
# "st" stays despite the homograph risk: "St. Louis"-class place names are
# far more common in this prose than a street abbreviation at sentence end,
# and the numeric rule below cannot save a name followed by a capital.
_ABBREV_ALWAYS = (
    "mr", "mrs", "ms", "dr", "prof", "rev", "hon", "sr", "jr", "st",
    "vs", "vol", "ave", "blvd", "rd", "ln", "ct", "mt",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "mon", "tue", "thu", "fri",
    "e.g", "i.e", "cf", "approx",
)
# Homographs of common words that are abbreviations only in numeric contexts:
# "No. 7", "Fig. 3", "Sat. 4 p.m.". Their period is protected only when a
# digit follows; "He stared at the sun. Then it was gone." splits normally.
_ABBREV_NUMERIC = ("no", "fig", "wed", "sat", "sun")
# These CAN end a sentence ("...by 4:15 p.m. Nobody left."), so their period is
# protected only when what follows is not the start of a new sentence.
_ABBREV_MEDIAL = (
    "a.m", "p.m", "etc", "inc", "ltd", "co", "corp", "dept", "est", "pp", "al", "ft",
    "u.s", "u.k",
)

_ABBREV_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(a) for a in _ABBREV_ALWAYS) + r")\.",
    re.I,
)
_ABBREV_NUMERIC_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(a) for a in _ABBREV_NUMERIC) + r")\.(?=\s*\d)",
    re.I,
)
# Protect only when NOT followed by whitespace + a capital or digit, i.e. only
# when it is genuinely mid-sentence.
#
# The `(?-i:...)` scope is load-bearing: this pattern is compiled with re.I so
# the abbreviation list matches any casing, but under IGNORECASE the class
# [A-Z0-9] also matches lowercase, which made the lookahead fire on ANY
# following letter and silently disabled the whole rule. The inline flag turns
# case-sensitivity back on for the class alone.
# The optional closer class lets the lookahead see a following capital
# through a closing quote — '"Bring maps, water, etc." He left.' is two
# sentences, and without it the protected period defeated the quote-final
# split branch downstream.
_ABBREV_MEDIAL_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(re.escape(a) for a in _ABBREV_MEDIAL) + r")\."
    r"(?![\"'”’)\]]*\s+(?-i:[A-Z0-9]))",
    re.I,
)
# A decimal point or a dotted initial ("J. R. Smith") is never a boundary.
_DECIMAL_RE = re.compile(r"\d\.\d")
_INITIAL_RE = re.compile(r"(?<!\w)[A-Z]\.")

# A terminator inside a closing quote splits only when what follows starts a
# new sentence. Splitting unconditionally turned every attributed line of
# dialogue -- speech ending in ! or ?, then a lowercase attribution -- into two
# "sentences", one of them a phantom fragment, which inflated fragment_rate and
# sentence variance for exactly the scene-heavy prose this style is built on.
_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])\s+"
    r"|(?<=[.!?])[\"'”’)\]]+\s+(?=[\"“'‘([A-Z0-9])"
    r"|\n{2,}"
)
_PROTECTED = "\x00"  # placeholder standing in for a non-terminal period
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
# A capitalized word. Sentence-initial capitals are stripped structurally in
# _count_proper_nouns rather than by lookbehind -- lookbehind cannot see past a
# leading quote or bracket, so `"Nobody told me` and newline-initial words were
# being counted as proper nouns and inflating specificity.
_CAPITALIZED = re.compile(r"\b[A-Z][a-z]{2,}\b")

# --- scene-craft -----------------------------------------------------------

# Quoted speech. A span cannot cross a quote mark or a line break, so one
# unbalanced quote cannot swallow the rest of the piece.
_QUOTED = re.compile(r"“[^”\n]+”|\"[^\"\n]+\"")
_SECOND_PERSON = re.compile(r"(?<!\w)(?:you|your|yours|yourself|yourselves)(?!\w)", re.I)
# Trailing quote/bracket characters that may follow a sentence's terminator.
_TRAILING_CLOSERS = "\"'”’)]}"


def _terminal_punct(sentence: str) -> str:
    """The sentence's terminating punctuation mark, ignoring trailing quotes."""
    stripped = sentence.rstrip().rstrip(_TRAILING_CLOSERS).rstrip()
    return stripped[-1] if stripped else ""


def _is_interjection(sentence: str) -> bool:
    """A very short exclamatory or interrogative sentence — the bark.

    Four words or fewer, ending in ! or ?. This is the interjected challenge
    or mock-question fired between longer runs, and it is scene-craft, not
    noise: rhythm metrics see it only as another fragment.
    """
    return _terminal_punct(sentence) in "!?" and len(_words(sentence)) <= 4


_EM_DASH = re.compile(r"—|--")
_ELLIPSIS = re.compile(r"\.\.\.|…")
# Shouting, not initialisms. An acronym like FEMA or DMV is ordinary reporting
# vocabulary; treating it as emphasis disqualified the elegiac band from any
# passage that named an agency. Require a run of two or more shouted words, or
# a single long one, to count as emphasis.
_ALLCAPS_TOKEN = re.compile(r"\b[A-Z]{3,}\b")
_SHOUT_RUN = re.compile(r"\b[A-Z]{2,}\b(?:[^A-Za-z\n]{1,3}\b[A-Z]{2,}\b)+")
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
    old young new fresh stale rotten black white red green blue grey gray brown
    yellow pale wet damp coarse crude blunt vast lush drab""".split()
)
# A modifier stack is introduced by a determiner or preposition ("a vicious,
# bloated, atavistic swine"). Requiring one is what separates a real stack from
# a verb followed by a gerund list ("the work involved filing, sorting, and
# shredding"), which morphology alone cannot distinguish.
_STACK_INTRODUCERS = frozenset(
    """a an the this that these those his her its their my our your no some any
    every each one two three four five several many few most other another of
    in on at with into through from by for like about across behind beneath
    beside under over toward against amid""".split()
)

# Words with adjective morphology that are almost never adjectives in practice.
_ADJ_STOP = frozenset(
    """the a an and or but if then than that this these those there here when
    where while very really only just also even still yet nearly almost
    something nothing anything everything morning evening during being having
    doing going nothing everybody somebody suddenly""".split()
)


def _sentences(text: str) -> list[str]:
    """Split into sentences, protecting periods that do not end one."""
    masked = text
    for pattern in (_ABBREV_RE, _ABBREV_NUMERIC_RE, _ABBREV_MEDIAL_RE, _DECIMAL_RE, _INITIAL_RE):
        masked = pattern.sub(lambda m: m.group(0).replace(".", _PROTECTED), masked)

    parts = (p.replace(_PROTECTED, ".").strip() for p in _SENTENCE_SPLIT.split(masked))
    return [p for p in parts if p]


def _words(text: str) -> list[str]:
    return _WORD.findall(text)


def _looks_adjectival(word: str) -> bool:
    low = word.lower()
    if low in _ADJ_STOP:
        return False
    # Check the closed list BEFORE the length guard. The guard exists to stop
    # short function words matching a suffix by accident, but applying it first
    # silently discarded every three-letter adjective in the list -- wet, dry,
    # hot, raw, odd, big, low, old, new, bad, mad raised no error, they simply
    # never counted.
    if low in _COMMON_ADJ:
        return True
    if len(low) < 4:
        return False
    return bool(_ADJ_SUFFIX.search(low))


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
        previous: str | None = None   # token immediately before the current run

        def flush(run: list[str], has_head: bool, introducer: str | None) -> int:
            """Score one candidate run of modifiers.

            A real adjective stack modifies a following noun, so `has_head`
            must be true -- a run that simply runs off the end of the sentence
            modifies nothing.

            `introducer` is the token before the run. A stack is introduced
            by a determiner or preposition; a run following a verb is a gerund
            list, not a stack, and morphology cannot tell the two apart.

            An all-gerund run is rejected outright as a second guard, since
            genuine stacks are essentially never composed entirely of -ing
            words.
            """
            if len(run) < min_run or not has_head:
                return 0
            if introducer is None or introducer.lower() not in _STACK_INTRODUCERS:
                return 0
            if all(w.lower().endswith("ing") for w in run):
                return 0
            adjectival = sum(1 for w in run if _looks_adjectival(w))
            return 1 if adjectival >= max(2, (len(run) + 1) // 2) else 0

        for tok in tokens:
            if tok == ",":
                preceded_by_comma = True
                continue
            # A word joins the run if it looks adjectival, or if a comma just
            # coordinated it onto an existing run.
            # A comma coordinates modifiers, but a conjunction or determiner
            # is not one -- letting those join dragged noun lists into runs.
            joins_by_comma = preceded_by_comma and run and tok.lower() not in _ADJ_STOP
            if _looks_adjectival(tok) or joins_by_comma:
                run.append(tok)
            else:
                # `tok` terminated the run, so it is the head noun. It is
                # non-adjectival by construction (an adjectival token would
                # have joined instead).
                stacks += flush(run, has_head=True, introducer=previous)
                previous = tok
                run = []
            preceded_by_comma = False

        # The sentence ended mid-run. If the run is long enough, its own last
        # member is the head noun ("long, low, filthy building"); otherwise
        # there is nothing being modified.
        if len(run) > min_run:
            stacks += flush(run[:-1], has_head=True, introducer=previous)
        else:
            stacks += flush(run, has_head=False, introducer=previous)
    return stacks


# --- register classification ----------------------------------------------


def _segment(text: str, target_sentences: int = 4) -> list[str]:
    """Split into scoring segments — paragraphs, or runs of sentences.

    Word-less paragraphs — scene dividers like '* * *', a lone em dash, a
    markdown rule — are dropped, not segmented. Classified, each one became a
    phantom 'clinical' segment sandwiched between real bands, so a one-note
    piece with ordinary section breaks manufactured enough fake register
    transitions to pass the motion gate.
    """
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if _words(p)]
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
    excite = len(_EXCLAIM.findall(segment)) + len(_SHOUT_RUN.findall(segment))
    # The interjected bark — a sentence of four words or fewer ending in ! or ?
    # — is manic evidence: it is how acceleration sounds on the page. Weighted
    # below a cue hit and capped, so a single "Why not?" cannot flip a segment
    # by itself. Interrogative barks deliberately do NOT touch the elegiac
    # excitement gate: a short plaintive question is legitimate mourning, while
    # an exclamation mark never is (it already counts through `excite`).
    interjections = sum(1 for s in sents if _is_interjection(s))

    # Structural signals. Weighted to roughly match cue magnitudes so neither
    # source dominates the other.
    scores["clinical"] += min(specifics / 4.0, 2.0)
    scores["clinical"] += 0.6 if adj_rate < 0.5 else 0.0
    scores["manic"] += 1.2 if mean_len > 26 else 0.0
    scores["manic"] += min(excite * 0.4, 1.2)
    scores["manic"] += min(interjections * 0.3, 0.9)
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


def _count_proper_nouns(text: str) -> int:
    """Capitalized words that are not merely sentence-initial."""
    total = 0
    for sentence in _sentences(text):
        # Drop the opening token structurally: any leading quote or bracket
        # means a lookbehind cannot tell an opener from a name.
        stripped = sentence.lstrip("\"'([{ \t")
        first = _WORD.search(stripped)
        for match in _CAPITALIZED.finditer(stripped):
            if first is not None and match.start() == first.start():
                continue
            total += 1
    return total


def _count_specifics(text: str) -> int:
    """Hard anchoring detail: distinct numbers, money, times, measures, names.

    Counts non-overlapping spans. The numeric patterns deliberately overlap --
    "$4.3 million at 4:15" is matched by _CURRENCY, _MEASURE, _TIME *and*
    _NUMERAL -- so summing the per-pattern counts inflated density by roughly
    2-3x and made the anchoring gate far easier to clear than intended.
    """
    spans: list[tuple[int, int]] = []
    for pattern in (_CURRENCY, _TIME, _PERCENT, _MEASURE, _NUMERAL):
        spans.extend(m.span() for m in pattern.finditer(text))

    spans.sort()
    distinct = 0
    covered_to = -1
    for start, end in spans:
        if start >= covered_to:      # a genuinely new fact
            distinct += 1
            covered_to = end
        elif end > covered_to:       # extends one already counted
            covered_to = end

    return distinct + _count_proper_nouns(text)


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
    # Scene-craft: measured and reported, never gated. The voice can carry an
    # entire piece with no dialogue in it, so none of these has a threshold —
    # they exist so a report can *see* the scene axis instead of flying blind.
    dialogue_share: float        # fraction of words inside quoted speech
    question_rate: float         # fraction of sentences ending in ?
    interjection_rate: float     # fraction of sentences that are interjections
    second_person_rate: float    # you/your per 100 words of narration
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
            f"  scene        dialogue {self.dialogue_share:.0%} of words   "
            f"questions {self.question_rate:.0%}, interjections "
            f"{self.interjection_rate:.0%} of sentences   "
            f"2nd person {self.second_person_rate:.2f}/100w",
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
            exclamation_rate=0.0, dialogue_share=0.0, question_rate=0.0,
            interjection_rate=0.0, second_person_rate=0.0,
            tic_count=0, tic_rate_per_500=0.0, tic_budget=0.0,
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

    def _tic_pattern(tic: str) -> re.Pattern[str]:
        # Phrasal tics need the same edge guards as single words: a bare
        # substring match found 'king hell' inside "walking hell" and 'the
        # whole thing' inside "the whole thingamajig", flipping passing prose
        # to FAIL on tics it never used.
        return re.compile(rf"(?<!\w){re.escape(tic)}(?!\w)")

    for tic in spec.tic_words:
        tic_count += len(_tic_pattern(tic).findall(low))
    for sentence in sents:
        s_low = sentence.lower()
        in_sentence = [tic for tic in spec.tic_words if _tic_pattern(tic).search(s_low)]
        if len(in_sentence) > int(spec.tics["max_per_sentence"]):
            violations.append(f"{len(in_sentence)} signature words in one sentence: {in_sentence}")

    tic_budget = spec.tic_budget_per_500 * max(n_words / 500, 0.2)
    tic_rate = tic_count / max(n_words / 500, 0.2)

    # -- scene-craft
    quoted_words = sum(len(_words(m.group(0))) for m in _QUOTED.finditer(text))
    narration = _QUOTED.sub(" ", text)
    narration_words = len(_words(narration))

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
        allcaps_rate=len(_ALLCAPS_TOKEN.findall(text)) / per_100,
        exclamation_rate=len(_EXCLAIM.findall(text)) / per_100,
        dialogue_share=quoted_words / n_words,
        question_rate=sum(1 for s in sents if _terminal_punct(s) == "?") / n_sents,
        interjection_rate=sum(1 for s in sents if _is_interjection(s)) / n_sents,
        second_person_rate=(
            len(_SECOND_PERSON.findall(narration)) / (narration_words / 100)
            if narration_words else 0.0
        ),
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
