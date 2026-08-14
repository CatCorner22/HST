# Design notes

## The problem

The brief was a *believable* engine that mirrors a distinctive prose style, and
an explicit instruction *not to impersonate* the writer. Those pull against each
other. Everything here follows from resolving that tension in a specific way.

## Style, not identity

The line this engine draws:

| Encoded | Not encoded |
|---|---|
| Sentence-rhythm mechanics | Any passage of his prose |
| Register bands and how they move | His biography, family, house, death |
| The specificity discipline | His name as a byline or speaker |
| Rhetorical devices — aimed hyperbole, bestial imagery, adjective stacking | Anything framed as his words |
| Structural habits — digression, the pretext, immediacy artifacts | Fabricated quotations |

This is a legal line and an engineering line at once, which is convenient.
Copyright protects a particular expression, not a style or a voice (see
`SOURCES.md`). And a rule system beats a corpus on every axis that matters here:
it can be read, argued with, tuned, unit-tested, and scored. A pile of scraped
prose can do none of that, and would have put the project on the wrong side of
the line for no benefit.

The persona is an original correspondent — same trade, same century as the
reader, own identity. It carries the stance and the mechanics and none of the
biography.

## The believability thesis

Most attempts at this voice fail the same way: they treat it as a vocabulary
list. Drugs, profanity, "swine", and a bat-country reference, at maximum volume,
start to finish.

The research says the real signature is elsewhere:

1. **Specificity.** Exaggeration anchored to exact times, prices, names, and
   quantities. The reader accepts the impossible part because the surrounding
   detail is so aggressively precise that the narrator's authority is already
   established. Unanchored hyperbole is noise.
2. **Rhythm dynamics.** Long accumulating clause-chains punched by four-word
   fragments. Learned by ear — he typed out *Gatsby* and *A Farewell to Arms*
   whole to feel how the sentences moved, and called it music.
3. **The elegiac break.** The sudden plain, unadorned, mournful passage in the
   middle of the grotesquerie. It is the hardest thing in the style to write and
   the first thing imitators drop.
4. **An aimed argument.** The comedy delivers an indictment. Savagery without a
   target is a costume.

So: **believability comes from dynamics and precision, not intensity.** That
thesis is not merely asserted in the prompt. It is measured, and it is enforced.

## Why there is a scorer

A prompt is a hope. An engine measures whether the hope came true.

`scoring/metrics.py` is deterministic and dependency-free: rhythm variance,
specificity density, adjective stacking, tic discipline, register segmentation.
`scoring/judge.py` handles what counting cannot — whether the savagery is aimed,
whether the elegiac passage lands or was bolted on, whether there is an argument
underneath. `modes/compose.py` feeds both back into a revision loop.

The metrics are a **floor check**: hitting every threshold scores 100 because the
thresholds are minimums, not a quality ceiling. The gradient above the floor
comes from the judge. Two gates, two different jobs.

**Negative controls are mandatory.** A scorer that always passes is worthless, so
the suite pins both directions:

| Fixture | Score | Outcome |
|---|---|---|
| `flat.txt` — corporate minutes | 39.2 | fails: uniform rhythm, unanchored, no stacking |
| `pastiche.txt` — all tics, no substance | 48.8 | fails: tic budget blown 43×, zero specifics |
| `no_elegiac.txt` — good prose, no break | 83.0 | **fails: no elegiac break** |
| `target.txt` — written to spec | 100.0 | passes |

The third row is the thesis under test. That fixture is genuinely well made —
varied rhythm, dense specifics, a real target — and it fails anyway, on the one
axis the research says separates the style from its imitations.

## Two calibration bugs worth recording

Both were caught by the negative controls, which is the argument for having them.

**Substring cue matching.** Register cues were matched with `cue in text`, so
`"ended"` fired on `"att**ended**"` and `"lost"` on `"c**los**est"`. Flat
corporate minutes were being classified as elegiac. Fixed with word-boundary
matching.

**Plainness is not mourning.** Even after that fix, the elegiac heuristic keyed
on structure — short sentences, no ornament, no shouting — which describes
administrative prose exactly as well as it describes grief. Two rounds of
narrowing followed: bare past tense went first, then single polysemous words
("never **once** cited", "has **lost** this vote" are not elegiac). What survives
is mostly phrasal, because in English the register is marked by a construction
rather than by vocabulary.

## Engineering variance without `temperature`

`temperature`, `top_p`, and `top_k` are rejected with a 400 on Claude Opus 5.
There is no sampling knob.

The replacement is `style/variance.py`: a seeded **Variance Director** that draws
a per-request assignment — opening move, dominant register, mandated contrast
register, imagery domain, and for long-form a structural template. This turned
out better than a temperature dial in three ways:

- **Reproducible.** Same seed, same directive, same piece.
- **Inspectable.** Every generation logs exactly what it was told to do. When a
  piece disappoints you can see which assignment produced it.
- **Aimed at the right axes.** It varies structure and register — the things that
  actually make two pieces feel different — rather than jittering token
  probabilities and hoping for the best.

Two rules are hardcoded in the director rather than left to chance: the elegiac
band is never dominant (it earns its force as contrast), and long-form always
mandates it (a filed piece without the break reads as pastiche).

## Prompt caching, and the constraint it imposes

The system prefix — persona, spec, guardrails — is ~2,950 tokens and identical on
every request. It carries a `cache_control` breakpoint, so from the second turn
on it bills at roughly a tenth of input price.

That imposes a hard architectural rule: **nothing dynamic may touch the system
prompt.** One timestamp, session id, or seed in that prefix silently destroys
caching for every downstream token — no error, just a bill. This is why the
variance directive lives in `messages` despite reading like a system
instruction, and why `tests/test_spec.py` asserts byte-stability and scans the
prefix for date- and hash-shaped strings.

## Guardrails, and testing them honestly

The rules are in `persona.py`, last in the system prompt so nothing in the style
guidance can be read as loosening them.

`tests/test_guardrails.py` is split on purpose. Offline tests assert the
*contract* — the rules exist, are unambiguous, override user instructions, and
reach the model. Live tests, marked `live` and skipped without credentials,
assert actual *behavior* under adversarial prompting. There is no honest way to
verify the second kind without calling the model, and an offline pass is evidence
the guardrails were asked for, not evidence they hold.

One guardrail exists to protect against the *other* failure mode: the engine is
explicitly permitted to discuss Thompson as a subject. Over-broad rules that
refuse the topic entirely would make the tool useless and are their own kind of
bug — so that permission is tested too.

## Known limitations

- **Register classification is heuristic.** Word-boundary cue matching plus
  structural signals, no POS tagger or classifier model. It is calibrated against
  four fixtures, which is enough to be useful and not enough to be authoritative.
- **The fact-preservation check is deliberately narrow.** It compares numeric
  tokens between source and output, so a `12%` restyled to "twelve percent" gets
  flagged. It is a prompt to look, not proof of loss. A full claim audit needs a
  model in the loop.
- **The adjective-stack detector has no parser.** Morphological suffixes plus a
  closed word list plus comma-coordination. It detects stacking, which is all it
  needs to do; it would be wrong about many other things.
- **The metrics ceiling is low.** Hitting every threshold yields 100. That is
  intentional — they are a floor — but it means the deterministic score alone
  cannot rank two good pieces. Use the judge for that.
- **Sessions are in-process.** `server.py` holds chat state in a dict with no
  eviction. Fine for a local tool, wrong for a deployment.

## Verification status

Verified in the build environment:

- 74 offline tests pass — scorer calibration in both directions, variance
  determinism and spread, prompt-cache byte-stability, request shape, mode
  wiring, guardrail contract.
- Request construction checked against a mocked SDK: no sampling parameters, a
  cache breakpoint on the system block, `effort` nested in `output_config`,
  refusal fallbacks opted in.
- Server boots; UI, static assets, and the metrics-only scoring path all serve.
- Error paths return actionable messages rather than tracebacks or hangs.

**Not verified here: anything requiring the API.** The build environment has no
Anthropic credentials, so no prose was actually generated, the revision loop
never ran end to end, the rubric judge was never invoked against a live model,
and the nine adversarial guardrail tests were collected but not executed.

Two bugs found offline are a fair indication of what that gap can hide. A
`max_tokens` of 32,000 on a non-streaming call raises `ValueError` before the
request is sent, which would have broken every `gonzo write` invocation; and
missing credentials surfaced as a raw `TypeError` from inside SDK header
validation. Both are fixed and both now have regression tests. Neither would
have appeared in a purely static review.

To close the gap, set a key and run:

```bash
pytest -m live                                   # adversarial guardrails
gonzo write "the county zoning board meeting"    # generation + revision loop
gonzo score <that output>                        # the engine judging itself
```

Confirm `usage.cache_read_input_tokens > 0` from turn two onward — that is the
proof the cached prefix is live rather than silently re-billed.
