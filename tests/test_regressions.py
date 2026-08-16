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
            # The truthful final-message shape for a fallback stream: the
            # abandoned text, the fallback marker, then the standing reply.
            # (stream() now verifies the final message carries standing text
            # — an empty-content mock would trip the empty-completion guard.)
            abandoned = MagicMock(); abandoned.type = "text"; abandoned.text = "abandoned "
            hop = MagicMock(); hop.type = "fallback"
            kept_block = MagicMock(); kept_block.type = "text"; kept_block.text = "kept"
            kept_block.citations = None
            final.content = [abandoned, hop, kept_block]
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

    def test_longform_source_draws_the_register_contract_without_structure(self):
        """Grade and instruct on the same terms — but with the register
        contract only. The old assertion demanded a STRUCTURE template, which
        was itself a bug: compose templates mandate invented scenes, dialogue,
        and companions, all of which a fact-preserving restyle is forbidden to
        add (one draw in ten injected the banned companion). Transfer gets the
        long-form demands (genuine occupation, mandatory elegiac contrast) and
        no beats."""
        from gonzo.modes.transfer import Transferrer

        client = MagicMock()
        client.complete.return_value = MagicMock(text="restyled output")
        source = "word " * (LONGFORM_MIN_WORDS + 50)

        Transferrer(client=client).transfer(source, seed=3)
        prompt = client.complete.call_args.kwargs["messages"][-1]["content"]
        assert "genuinely occupy" in prompt, "long source must get the long-form register contract"
        assert "REQUIRED CONTRAST REGISTER — elegiac" in prompt
        assert "STRUCTURE" not in prompt, "compose templates must not enter a restyle"

    def test_gray_zone_source_gets_the_longform_contract(self):
        """The scorer judges the OUTPUT, and transfer's own budget lets the
        output run 50% over the source — so a 267-399-word source can yield a
        400+-word restyle. Instructing from source length alone failed those
        outputs for an elegiac break they were never asked to write."""
        from gonzo.modes.transfer import Transferrer

        client = MagicMock()
        client.complete.return_value = MagicMock(text="restyled output")
        source = "word " * 300  # legal output: up to 450 words = long-form

        Transferrer(client=client).transfer(source, seed=3)
        prompt = client.complete.call_args.kwargs["messages"][-1]["content"]
        assert "REQUIRED CONTRAST REGISTER — elegiac" in prompt

    def test_short_source_stays_shortform(self):
        from gonzo.modes.transfer import Transferrer

        client = MagicMock()
        client.complete.return_value = MagicMock(text="restyled output")
        source = "word " * 120  # even +50% cannot reach the long-form bar

        Transferrer(client=client).transfer(source, seed=3)
        prompt = client.complete.call_args.kwargs["messages"][-1]["content"]
        assert "genuinely occupy" not in prompt
        assert "STRUCTURE" not in prompt

    def test_time_without_space_before_meridiem_is_one_fact(self):
        """'4:15pm' shredded into '4' and '15' by the bare-number branch let a
        dropped signing time pass as preserved when other numbers coincided,
        and flagged a preserved one as dropped when only spacing changed."""
        from gonzo.modes.transfer import Transferrer

        dropped = Transferrer._dropped(
            "The deal was signed at 4:15pm, with 15 lawyers on Highway 4.",
            "The deal was signed — 15 lawyers, Highway 4, ink still wet.",
        )
        assert "4:15" in dropped, "the dropped time must be flagged"
        assert Transferrer._dropped("It happened at 4:15pm.", "It happened at 4:15 pm.") == []

    def test_spacing_only_changes_are_not_dropped_facts(self):
        from gonzo.modes.transfer import Transferrer

        assert Transferrer._dropped("It cost $ 4.3 million.", "It cost $4.3 million.") == []
        assert Transferrer._dropped("Up 12 %.", "Up 12%.") == []


class TestPhrasalTicBoundaries:
    """Multi-word tics were matched as raw substrings: 'king hell' fired
    inside "walking hell" and 'the whole thing' inside "the whole
    thingamajig", flipping passing prose to FAIL on tics it never used."""

    def test_embedded_phrases_do_not_count(self):
        from gonzo.scoring.metrics import score_text

        report = score_text("The lobby was a walking hell of dead slot machines.")
        assert report.tic_count == 0
        report = score_text("He fixed the whole thingamajig with tape.")
        assert report.tic_count == 0

    def test_real_phrasal_tics_still_count(self):
        from gonzo.scoring.metrics import score_text

        assert score_text("It was king hell out there.").tic_count == 1
        assert score_text("Forget the whole thing.").tic_count == 1


class TestSceneDividers:
    """Word-less paragraphs ('* * *', '---', a lone dash) were classified as
    'clinical' segments, so a one-note piece with ordinary section breaks
    manufactured enough phantom register transitions to pass the motion
    gate."""

    def test_dividers_create_no_phantom_transitions(self):
        from gonzo.scoring.metrics import score_text

        manic = (
            "It went faster and faster, careening and screaming, wild and "
            "berserk, a frantic howling blur of panic and chaos. "
        ) * 8
        plain = score_text("\n\n".join([manic, manic, manic]))
        divided = score_text("\n\n* * *\n\n".join([manic, manic, manic]))
        assert divided.register_transitions == plain.register_transitions
        assert "clinical" not in divided.bands


class TestHomographAbbreviations:
    """'no', 'sun', 'sat', 'wed', 'fig' were unconditionally protected as
    abbreviations, merging real sentence boundaries — 'The answer was no.
    Nobody argued.' parsed as one sentence, deleting a genuine fragment and
    manufacturing false tic-adjacency violations across the false merge.
    They are now protected only in numeric contexts ('No. 7', 'Fig. 3'),
    and 'St.' keeps its unconditional protection for place names."""

    @pytest.mark.parametrize("text", [
        "He stared at the sun. Then it was gone.",
        "The answer was no. Nobody argued.",
        "They wed. The party ran until dawn.",
        "That is where he sat. The chair was still warm.",
        "I do not give a fig. Nobody does.",
    ])
    def test_homographs_split(self, text):
        from gonzo.scoring.metrics import _sentences

        assert len(_sentences(text)) == 2

    @pytest.mark.parametrize("text", [
        "The car was No. 7 in the line.",
        "See Fig. 3 for the layout.",
        "The sign said Sat. 4 p.m. sharp.",
        "St. Louis is a fine town.",
    ])
    def test_numeric_and_name_contexts_stay_whole(self, text):
        from gonzo.scoring.metrics import _sentences

        assert len(_sentences(text)) == 1

    def test_quote_final_medial_abbreviation_splits(self):
        """The medial lookahead could not see a capital through a closing
        quote, so dialogue ending in 'etc.' swallowed the next sentence."""
        from gonzo.scoring.metrics import _sentences

        assert len(_sentences('"Bring maps, water, etc." He left at dawn.')) == 2
        assert len(_sentences("We packed rope, tape, etc. and left.")) == 1


class TestCritiqueDegradesLikeCompose:
    """A judge decline (StructuredOutputError) crashed `gonzo score` with a
    traceback and /api/score with a bare 500, discarding metrics that were
    already computed. Critique now degrades to metrics-only, same as
    compose."""

    def test_structured_output_error_degrades_to_metrics(self):
        from gonzo.client import StructuredOutputError
        from gonzo.modes.critique import Critic

        client = MagicMock()
        client.parse.side_effect = StructuredOutputError(ValueError("declined"))
        report = Critic(client=client).critique("Some prose to score.")
        assert report.verdict is None
        assert report.metrics is not None


class TestEmptyStream:
    """stream() ended cleanly on a thinking-only/truncated turn while
    complete() raised — so ChatSession recorded an empty assistant turn, and
    the API rejects any later request whose history contains one: the
    session was bricked until reset."""

    def test_empty_stream_raises(self):
        from gonzo.client import EmptyCompletionError, GonzoClient

        with patch("anthropic.Anthropic") as ctor:
            sdk = MagicMock()
            ctor.return_value = sdk
            final = MagicMock()
            final.stop_reason = "max_tokens"
            final.content = []
            final.usage = MagicMock(
                input_tokens=1, output_tokens=1,
                cache_read_input_tokens=0, cache_creation_input_tokens=0,
            )
            ctx = MagicMock()
            entered = ctx.__enter__.return_value
            entered.__iter__ = lambda self: iter([])
            entered.get_final_message.return_value = final
            sdk.beta.messages.stream.return_value = ctx

            with pytest.raises(EmptyCompletionError):
                list(GonzoClient(api_key="k").stream(
                    system_prompt="SPEC", messages=[{"role": "user", "content": "x"}]
                ))


class TestUnseededVariance:
    """Both production chat surfaces default to seed=None, where the seed
    derived solely from the message text — 'go on' drew the identical
    directive every time it was typed, converging replies onto one register."""

    def test_repeated_message_draws_fresh_directives(self):
        from gonzo.modes.chat import ChatSession

        client = MagicMock()
        client.complete.return_value = MagicMock(text="a reply")
        session = ChatSession(client=client)  # no seed: the production default
        session.send("go on")
        first = session.last_directive
        session.send("go on")
        second = session.last_directive
        assert first.seed != second.seed


class TestServerSessionLocking:
    """FastAPI runs sync endpoints in a threadpool and ChatSession has no
    synchronization — a double-submit interleaved two streams through one
    session, corrupting history order. The endpoint now refuses concurrent
    turns on one session instead of interleaving them."""

    def test_second_concurrent_turn_is_refused(self):
        import threading as _threading

        from fastapi.testclient import TestClient

        import gonzo.server as server

        started = _threading.Event()
        release = _threading.Event()

        def slow_stream(_message):
            started.set()
            release.wait(timeout=5)
            yield "reply text"

        with TestClient(server.app) as web:
            first = web.post("/api/chat", json={"message": "hi"})
            session_id = first.text.split('"session_id": "')[1].split('"')[0]

            session = server._SESSIONS[session_id]
            session.stream = slow_stream

            results = {}

            def first_turn():
                results["first"] = web.post(
                    "/api/chat", json={"message": "one", "session_id": session_id}
                ).text

            t = _threading.Thread(target=first_turn)
            t.start()
            assert started.wait(timeout=5), "first stream never started"
            second = web.post(
                "/api/chat", json={"message": "two", "session_id": session_id}
            ).text
            release.set()
            t.join(timeout=5)

        assert '"kind": "busy"' in second, "concurrent turn must be refused, not interleaved"
        assert "reply text" in results["first"], "the first stream must finish normally"


class TestReadInputExitCodes:
    """A missing or undecodable input file exited 1 — the documented 'prose
    failed the bar' verdict — so scripts branching on the exit code read an
    I/O error as a style judgment. Usage and I/O failures exit 2."""

    def test_missing_file_exits_2(self):
        from gonzo.cli import _read_input

        with pytest.raises(SystemExit) as excinfo:
            _read_input("/nonexistent/definitely/not/here.txt")
        assert excinfo.value.code == 2

    def test_undecodable_file_exits_2(self, tmp_path):
        from gonzo.cli import _read_input

        bad = tmp_path / "latin.txt"
        bad.write_bytes("caf\xe9".encode("latin-1"))
        with pytest.raises(SystemExit) as excinfo:
            _read_input(str(bad))
        assert excinfo.value.code == 2


class TestPackaging:
    def test_web_assets_ship_inside_the_package(self):
        """WEB_DIR resolved beside the package, so a non-editable install served
        no UI at all."""
        assert WEB_DIR.is_dir()
        assert (WEB_DIR / "index.html").is_file()
        assert WEB_DIR.parent.name == "gonzo"
