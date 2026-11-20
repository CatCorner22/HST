# Gonzo Style Engine

[![tests](https://github.com/CatCorner22/HST/actions/workflows/test.yml/badge.svg)](https://github.com/CatCorner22/HST/actions/workflows/test.yml)

A prose engine in the gonzo tradition of American literary journalism — the
first-person, participant, openly-biased mode of reporting that Hunter S.
Thompson invented.

**It does not impersonate him.** The narrator is an original correspondent with
its own identity. What the engine encodes is a derived specification of
*stylistic mechanics* — rhythm, register, specificity, structure — never a corpus
of anyone's prose. Style and voice are not copyrightable; specific expression is.
The engine is built on the right side of that line, and it is also the better
engineering choice: a rule system can be inspected, tuned, and tested. See
[`docs/DESIGN.md`](docs/DESIGN.md) and [`docs/SOURCES.md`](docs/SOURCES.md).

The engine has two desks. **The Correspondent** writes in the tradition, as
itself. **The Morgue** — a newspaper's clipping archive — answers questions
*about* Thompson in plain scholarly prose, from a hand-checked quote bank and
fact file, and labels every quotation verified, secondary, or disputed.

## The thesis

Most attempts at this voice fail identically — drugs, profanity, "swine", maximum
volume start to finish. The research says the real signature is elsewhere:

1. **Specificity.** Exaggeration bolted to exact times, prices, names, and
   quantities. Unanchored hyperbole is noise.
2. **Rhythm dynamics.** Long accumulating clause-chains punched by four-word
   fragments.
3. **The elegiac break.** The sudden plain, unadorned passage in the middle of
   the grotesquerie — the hardest thing in the style, and the first thing
   imitators drop.
4. **An aimed argument.** The comedy carries an indictment.

**Believability comes from dynamics and precision, not intensity.** The engine
measures that and enforces it, rather than just asking for it.

## Install

```bash
pip install -e ".[dev]"
cp .env.example .env      # add your ANTHROPIC_API_KEY
```

An `ant auth login` profile works instead of a key — the SDK finds it on disk.

## Use

```bash
gonzo chat                                    # conversation, streaming
gonzo chat --wire                             # same, with live web search
gonzo morgue "where does 'too weird to live' come from?"  # the research desk
gonzo morgue                                  # research desk, interactive
gonzo write "the county zoning board meeting" # long-form, with revision loop
gonzo write --wire "the September jobs report"  # researched long-form
gonzo transfer draft.txt                      # restyle, preserving every fact
gonzo score piece.txt                         # measure any prose
gonzo score --no-judge piece.txt              # metrics only, no API call
gonzo serve                                   # web UI at http://127.0.0.1:8000
```

`chat`, `write`, and `transfer` take `--seed N` for reproducible output.
`chat`, `morgue`, and `write` take `--wire`.

### The wire

By default the narrator is an archivist with a cutoff: its knowledge is a file
with an end date, it says so plainly, and it never offers a search it cannot
run. `--wire` (or `GONZO_WIRE=1`, or the checkbox in the web UI) installs a
real one — the Anthropic server-side web search tool. With the wire on, the
persona swaps its capability section to match: the correspondent pulls the
wire for load-bearing facts past its file's end date, attributes what it
finds in the prose ("the AP had it that…"), and the engine lists the cited
sources under the reply. Searches are billed by the API on top of token cost,
which is why it is opt-in. The two personas never blend: the narrator's
claims about its machinery are true in both configurations, and a test
enforces it.

## The five modes

**Chat** — conversation in voice. Draws a fresh stylistic assignment each turn so
replies don't converge on one register.

**Compose** — a filed piece from an assignment, then a **score-then-revise loop**:
the draft is measured, its own diagnostics are handed back, and it is rewritten
until it clears the spec or runs out of attempts. Keeps the best draft, not the
last.

**Transfer** — restyles your text. Facts, figures, names, and the argument
survive intact; a numeric diff flags anything that went missing.

**Score** — the scorer on its own, against this engine's output or your own.

**Morgue** — the research desk. Sourced answers about Thompson's life, work,
and craft. It quotes him only from `gonzo/research/quotes.yaml` (25 entries,
each under 15 words, each with the URLs where the wording was checked) and
takes dates from `gonzo/research/facts.yaml`, which records conflicts between
sources instead of hiding them. Where the record is silent, it says so.

```
PASS  score 100.0/100   (412 words)

  rhythm       cv 0.80 (>= 0.55)   mean 20.6w   fragments 10%   longest 69w
  specificity  5.3/100w (>= 3.0)
  adjectives   0.24 stacks/100w (>= 0.15)
  scene        dialogue 0% of words   questions 0%, interjections 0% of sentences   2nd person 0.49/100w
  tics         0 used, 0.0/500w (budget 3.0)
  registers    clinical -> elegiac -> clinical
               2 transitions, elegiac present
  prosody      cliff-drops 0   still points 2   spaced rests 0   polysyndeton 2   capitals 0   elegiac monosyllables 78%   (reported, not gated)
```

## How it works

**The style spec** (`gonzo/style/style_spec.md`) is the core artifact: stance
(including the autopsy-of-a-promise frame), topic independence (the subject is
never the limit — power scales down, love is a legitimate register, forced
outrage is pastiche), the Rhythm Law, the Specificity Anchor, four register
bands, scene and dialogue, the paranoid hypothetical, the small moves
(interjection, self-address, second-person pull, present-tense lurch, wisdom
line), the two-stroke engine (the cliff-drop, the still point, the elegiac
cadence), eight budgeted sentence devices, the anti-pastiche budget, and the
prohibitions. It becomes the cached system prompt.

**The Variance Director** (`gonzo/style/variance.py`) replaces `temperature` —
which Opus 5 rejects outright. Each request draws a seeded assignment: opening
move, dominant register, mandated contrast register, imagery domain, structural
template, a focus pair of sentence devices, and — in chat — a conversational
stance. Reproducible by seed, inspectable after the fact, and aimed at the axes
that actually make two pieces feel different.

**The scorer** (`gonzo/scoring/`) is what makes this an engine rather than a
prompt. Deterministic metrics measure rhythm variance, specificity density,
adjective stacking, tic discipline, and register motion, and report (without
gating) the prosody of the two-stroke engine. An LLM rubric judge covers what
counting can't: whether the savagery is aimed, whether the elegiac break lands,
whether the acceleration drops unannounced, whether there's an argument under
the comedy.

**Prompt caching** keeps it cheap. The ~6,900-token system prefix (the Morgue's
is ~5,500) is byte-stable and cached; only the variance directive and the conversation vary. Hence the hard
rule that nothing dynamic may touch the system prompt — a test enforces it.

## Tests

```bash
pytest              # 273 offline tests, no API key needed
pytest -m live      # 9 adversarial guardrail tests, needs credentials
```

CI runs the offline suite on Python 3.11, 3.12 and 3.13, re-checks the fixture
calibration explicitly, and installs a built wheel into a clean virtualenv to
confirm the style data and web assets are actually packaged — an editable
install cannot catch that, and one such bug already shipped.

The suite pins the scorer in **both** directions, because a scorer that always
passes is worthless. Every bug found in the adversarial sweep
([`docs/DESIGN.md`](docs/DESIGN.md)) also carries a regression guard, and every
guard is mutation-tested — reverted in turn to confirm it actually fails:

| Fixture | Score | Outcome |
|---|---|---|
| flat corporate minutes | 39.2 | fails — uniform rhythm, unanchored |
| all-tic pastiche | 34.8 | fails — tic budget blown |
| **good prose, no elegiac break** | **83.0** | **fails — the thesis under test** |
| written to spec (quiet face: clinical/elegiac) | 100.0 | passes |
| written to spec (loud face: savage/manic, dialogue) | 100.0 | passes |
| written to spec (domestic face: a diner buyout, zero politics, zero tics) | 100.0 | passes |

## What it will not do

Claim to be Hunter S. Thompson — neither desk will, and both say so when asked.
Reproduce his published prose. Write under his byline. Fabricate biography about
him or anyone else. Offer machinery it does not have: without the wire it says
its file has an end date and stops there; with the wire it cites what it pulls.

The Correspondent never quotes him. The Morgue quotes him only from its bank of
short, sourced quotations, labels each one's verification status, and never
reconstructs a quotation from memory.

Both desks **will** discuss him freely — craft, influence, what the tradition
owes him. Analysis is not impersonation, and guardrails broad enough to refuse
the subject would be their own bug. That permission is tested too.

## Layout

```
gonzo/
  style/     style_spec.md · lexicon.yaml · structures.yaml · variance.py
  research/  morgue.py · quotes.yaml (quote bank) · facts.yaml (fact file)
  scoring/   metrics.py (deterministic) · judge.py (rubric) · report.py
  modes/     chat · compose · transfer · critique · morgue
  web/       browser UI, no build step (inside the package so wheels ship it)
  client.py  caching, streaming, refusal handling
  cli.py · server.py
docs/        DESIGN.md · SOURCES.md
```
