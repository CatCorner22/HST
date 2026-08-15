"""Regression guards for bugs found in the adversarial debug sweep.

Each test names the defect it pins. Several of these bugs were invisible to the
original suite because they lived on API paths that no offline test exercised —
so where the defect needs an API response shape, it is reconstructed with mocks
rather than left unguarded.
"""

from unittest.mock import MagicMock, patch

import pytest

from gonzo.client import (
    EmptyCompletionError,
    GonzoClient,
    StreamReset,
    _text_of,
)
from gonzo.config import LONGFORM_MIN_WORDS, WEB_DIR
from gonzo.scoring.judge import JudgeVerdict
from gonzo.scoring.metrics import (
    _adjective_stacks,
    _count_proper_nouns,
    _count_specifics,
    _looks_adjectival,
    _sentences,
    _SHOUT_RUN,
)
from gonzo.style.variance import VarianceDirector


def _block(kind, text=None):
    b = MagicMock()
    b.type = kind
    if text is not None:
        b.text = text
    return b


class TestFallbackSplicing:
    """A declined model's abandoned output must not be glued to the reply.

    Every request opts into server-side fallbacks. When one fires, the API
    returns ONE message whose content is
    [<declining model's blocks>, fallback, <replacement's blocks>] with
    stop_reason "end_turn" — a normal success. Concatenating every text block
    handed back a spliced half-sentence as a clean result.
    """

    def test_text_after_the_last_fallback_wins(self):
        response = MagicMock()
        response.content = [
            _block("text", "The road to Vegas was a bad "),
            _block("fallback"),
            _block("text", "A cleaner opening from the replacement."),
        ]
        assert _text_of(response) == "A cleaner opening from the replacement."

    def test_multiple_hops_keep_only_the_last(self):
        response = MagicMock()
        response.content = [
            _block("text", "first hop "),
            _block("fallback"),
            _block("text", "second hop "),
            _block("fallback"),
            _block("text", "the reply that stands"),
        ]
        assert _text_of(response) == "the reply that stands"

    def test_no_fallback_is_unaffected(self):
        response = MagicMock()
        response.content = [_block("thinking"), _block("text", "ordinary reply")]
        assert _text_of(response) == "ordinary reply"


class TestEmptyCompletion:
    """A thinking-only or truncated response must not be recorded as a reply."""

    def test_empty_extraction_raises(self):
        with patch("anthropic.Anthropic") as ctor:
            sdk = MagicMock()
            ctor.return_value = sdk
            response = MagicMock()
            response.content = [_block("thinking")]     # no text block at all
            response.stop_reason = "max_tokens"
            ctx = MagicMock()
            ctx.__enter__.return_value.get_final_message.return_value = response
            sdk.beta.messages.stream.return_value = ctx

            with pytest.raises(EmptyCompletionError) as excinfo:
                GonzoClient(api_key="k").complete(
                    system_prompt="SPEC", messages=[{"role": "user", "content": "x"}]
                )
            assert "max_tokens" in str(excinfo.value)


class TestStreamHopBoundary:
    """stream() must signal that earlier output was superseded."""

    def test_fallback_boundary_yields_a_reset(self):
        start_fallback = MagicMock()
        start_fallback.type = "content_block_start"
        start_fallback.content_block = MagicMock(type="fallback")

        def delta(text):
            e = MagicMock()
            e.type = "content_block_delta"
            e.delta = MagicMock(type="text_delta", text=text)
            return e

        with patch("anthropic.Anthropic") as ctor:
            sdk = MagicMock()
            ctor.return_value = sdk
            final = MagicMock()
            final.stop_reason = "end_turn"
            final.usage = MagicMock(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            )
            ctx = MagicMock()
            entered = ctx.__enter__.return_value
            entered.__iter__ = lambda self: iter([delta("abandoned "), start_fallback, delta("kept")])
            entered.get_final_message.return_value = final
            sdk.beta.messages.stream.return_value = ctx

            out = list(GonzoClient(api_key="k").stream(
                system_prompt="SPEC", messages=[{"role": "user", "content": "x"}]
            ))

        assert any(isinstance(c, StreamReset) for c in out)
        after = out[[isinstance(c, StreamReset) for c in out].index(True) + 1 :]
        assert "".join(after) == "kept"


class TestSentenceSplitting:
    """Splitting on every period corrupted every rhythm metric downstream."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Mr. Smith went to Washington. He stayed.", 2),
            ("It cost $2.1 million. That is a lot.", 2),
            ("It ran to 4:15 p.m. and then stopped.", 1),
            ("The meeting ran to 4:15 p.m. Nobody left.", 2),
            ("J. R. Ewing called at noon. Twice.", 2),
            ("Acme Inc. filed the papers.", 1),
            ("He worked at Acme Inc. Then he quit.", 2),
            ("The vote was 4-1. The dissent came later.", 2),
        ],
    )
    def test_counts(self, text, expected):
        assert len(_sentences(text)) == expected

    def test_ignorecase_does_not_disable_the_capital_lookahead(self):
        """The lookahead class is scoped `(?-i:...)`.

        Under re.I, [A-Z0-9] also matches lowercase, which made the "not
        followed by a capital" guard fire on ANY following letter and silently
        disabled abbreviation protection entirely.
        """
        assert len(_sentences("It ran to 4:15 p.m. and stopped.")) == 1


class TestSpecificityCounting:
    def test_overlapping_patterns_count_once(self):
        """"$4.3 million at 4:15" is two facts, not the four that the
        per-pattern sum reported."""
        assert _count_specifics("It cost $4.3 million at 4:15.") == 2

    def test_sentence_initial_capitals_are_not_proper_nouns(self):
        assert _count_proper_nouns("The meeting ended. Dogs barked.") == 0

    def test_quote_initial_openers_are_excluded(self):
        """A lookbehind cannot see past a leading quote; the split is structural."""
        assert _count_proper_nouns('"Nobody told me. He left for Tulare."') == 1

    def test_mid_sentence_names_count(self):
        assert _count_proper_nouns("He met Smith and Jones in Fresno.") == 3


class TestShoutingVsAcronyms:
    """An acronym is reporting vocabulary, not emphasis.

    Treating every ALLCAPS token as shouting disqualified the elegiac band from
    any passage that named an agency.
    """

    def test_lone_acronym_is_not_shouting(self):
        assert _SHOUT_RUN.findall("The FEMA office denied it.") == []

    def test_a_run_of_capitals_is_shouting(self):
        assert _SHOUT_RUN.findall("This was TOTAL UTTER nonsense.") == ["TOTAL UTTER"]


class TestAdjectiveStacks:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("a vicious bloated atavistic swine", 1),
            ("a thick, sweet, ammoniac wall", 1),
            ("a long, low, filthy building", 1),
            ("through the wet, black, stinking yard", 1),
            ("the work involved filing, sorting, and shredding", 0),
            ("duties include invoicing, reporting, and auditing.", 0),
            ("the big red car", 0),
        ],
    )
    def test_counts(self, text, expected):
        assert _adjective_stacks(text) == expected

    def test_short_adjectives_are_recognised(self):
        """The length guard used to run before the closed-list lookup, so every
        three-letter adjective in the list was silently discarded."""
        assert all(_looks_adjectival(w) for w in ("wet", "dry", "hot", "raw", "odd", "big"))


class TestJudgeClamping:
    """ge/le are stripped from the wire schema, so an out-of-range score used to
    raise ValidationError and discard a finished draft."""

    def test_out_of_range_scores_clamp(self):
        v = JudgeVerdict(
            aim=11, anchoring=-3, register_control=7, elegiac_quality=5,
            argument=8, originality=7, scene_craft=12, velocity=6, comedy=-1,
            reads_as_pastiche=False,
            strongest="s", weakest="w", fixes=[],
        )
        assert v.aim == 10
        assert v.anchoring == 0
        assert v.scene_craft == 10
        assert v.comedy == 0


class TestSeedRoundTrip:
    """Seeds are shown in the web UI and pasted back to reproduce a run, so they
    must survive JSON in a browser. JS rounds anything above 2**53."""

    def test_seeds_are_js_safe(self):
        director = VarianceDirector()
        seeds = [director.draw(basis=f"assignment {i}").seed for i in range(200)]
        assert max(seeds) < 2**53
        assert all(s == int(float(s)) for s in seeds)


class TestTransferParity:
    def test_currency_regex_excludes_trailing_punctuation(self):
        from gonzo.modes.transfer import Transferrer

        assert Transferrer._dropped("It cost $4.3 million.", "They paid $4.3 million.") == []

    def test_longform_source_draws_a_longform_directive(self):
        """Grade and instruct on the same terms: the scorer treats anything over
        LONGFORM_MIN_WORDS as long-form, so the directive must match."""
        from gonzo.modes.transfer import Transferrer

        client = MagicMock()
        client.complete.return_value = MagicMock(text="restyled output")
        source = "word " * (LONGFORM_MIN_WORDS + 50)

        Transferrer(client=client).transfer(source, seed=3)
        prompt = client.complete.call_args.kwargs["messages"][-1]["content"]
        assert "STRUCTURE" in prompt, "long source must draw a long-form directive"


class TestPackaging:
    def test_web_assets_ship_inside_the_package(self):
        """WEB_DIR resolved beside the package, so a non-editable install served
        no UI at all."""
        assert WEB_DIR.is_dir()
        assert (WEB_DIR / "index.html").is_file()
        assert WEB_DIR.parent.name == "gonzo"
