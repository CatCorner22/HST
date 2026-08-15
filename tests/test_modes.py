"""Mode wiring: where the directive goes, and what history keeps."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gonzo.client import WireSource
from gonzo.config import wire_tool
from gonzo.modes.chat import ChatSession
from gonzo.modes.compose import Composer
from gonzo.modes.transfer import Transferrer

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.complete.return_value = MagicMock(text="a reply")
    return client


class TestChatSession:
    def test_directive_goes_in_messages_not_system(self, fake_client):
        """Cache safety: the directive changes every turn, so it must sit after
        the breakpoint or it invalidates the cached prefix on every request."""
        session = ChatSession(client=fake_client, seed=5)
        session.send("what about the airport")

        kwargs = fake_client.complete.call_args.kwargs
        assert "style_directive" not in kwargs["system_prompt"]
        assert "<style_directive>" in kwargs["messages"][-1]["content"]

    def test_history_stores_the_clean_turn(self, fake_client):
        """Scaffolding is per-request. Keeping it in history would bloat context
        and let a stale assignment bleed into later turns."""
        session = ChatSession(client=fake_client, seed=5)
        session.send("first question")

        assert session.history[0] == {"role": "user", "content": "first question"}
        assert session.history[1] == {"role": "assistant", "content": "a reply"}

    def test_directive_changes_between_turns(self, fake_client):
        """A fixed session seed must still produce motion across a conversation,
        or every reply lands in the same register."""
        session = ChatSession(client=fake_client, seed=5)
        session.send("one")
        first = session.last_directive
        session.send("two")
        assert session.last_directive.seed != first.seed

    def test_seeded_session_is_reproducible(self, fake_client):
        def run():
            s = ChatSession(client=fake_client, seed=99)
            s.send("same question")
            return s.last_directive.to_dict()

        assert run() == run()

    def test_reset_clears_history_and_turn_counter(self, fake_client):
        session = ChatSession(client=fake_client, seed=5)
        session.send("one")
        session.reset()
        assert session.history == []
        session.send("one")
        # The turn counter resets too, so the seed sequence restarts at the
        # session seed rather than continuing from where it left off.
        assert session.last_directive.seed == 5

    def test_prior_history_is_replayed(self, fake_client):
        session = ChatSession(client=fake_client, seed=5)
        session.send("first")
        session.send("second")
        sent = fake_client.complete.call_args.kwargs["messages"]
        assert sent[0]["content"] == "first"
        assert sent[1]["content"] == "a reply"


class TestChatWire:
    def test_wireless_by_default(self, fake_client):
        """No tools, and the persona that denies having any — the pairing is
        the contract, in both directions."""
        session = ChatSession(client=fake_client, seed=5)
        session.send("what about the airport")

        kwargs = fake_client.complete.call_args.kwargs
        assert kwargs["tools"] is None
        assert "no search desk" in " ".join(kwargs["system_prompt"].split())

    def test_wire_attaches_tool_and_swaps_persona_together(self, fake_client):
        session = ChatSession(client=fake_client, seed=5, wire=True)
        session.send("what about the airport")

        kwargs = fake_client.complete.call_args.kwargs
        assert kwargs["tools"] == [wire_tool()]
        normalized = " ".join(kwargs["system_prompt"].split())
        assert "The desk has installed a wire" in normalized
        assert "no search desk" not in normalized

    def test_records_cited_sources_but_keeps_history_clean(self, fake_client):
        source = WireSource(url="https://apnews.com/x", title="AP")
        fake_client.complete.return_value = MagicMock(text="a reply", sources=[source])
        session = ChatSession(client=fake_client, seed=5, wire=True)
        session.send("what happened today")

        assert session.last_sources == [source]
        # Search plumbing must not enter history — the next turn re-sends
        # history verbatim, and stale tool blocks would be re-billed and
        # re-interpreted as conversation.
        assert session.history[1] == {"role": "assistant", "content": "a reply"}


class TestComposeWire:
    def test_draft_carries_its_sources(self):
        source = WireSource(url="https://sec.gov/filing", title="10-K")
        client = MagicMock()
        client.complete.return_value = MagicMock(text="the piece", sources=[source])
        composer = Composer(client=client, use_judge=False, wire=True)

        draft = composer.compose("the hearing", revise=False)

        assert draft.sources == [source]
        assert client.complete.call_args.kwargs["tools"] == [wire_tool()]

    def test_wireless_compose_sends_no_tools(self):
        client = MagicMock()
        client.complete.return_value = MagicMock(text="the piece", sources=[])
        composer = Composer(client=client, use_judge=False)
        composer.compose("the hearing", revise=False)
        assert client.complete.call_args.kwargs["tools"] is None

    def test_revision_sources_accumulate_and_deduplicate(self):
        """The surviving draft owes its facts to every round that fed it, so
        sources accumulate — but one URL cited in two rounds is one source."""
        source_a = WireSource(url="https://a.example/1", title="A")
        source_b = WireSource(url="https://b.example/2", title="B")
        # First draft: flat prose that fails the metrics. Revision: the target
        # fixture, which passes — so the revision deterministically wins.
        winning = (FIXTURES / "target.txt").read_text(encoding="utf-8")
        client = MagicMock()
        client.complete.side_effect = [
            MagicMock(text="It is what it is. " * 40, sources=[source_a]),
            MagicMock(text=winning, sources=[source_a, source_b]),
        ]
        composer = Composer(client=client, use_judge=False, wire=True)

        draft = composer.compose("the hearing")

        assert draft.report.passed, "the revision fixture must win for this test to bite"
        assert draft.sources == [source_a, source_b]


class TestWireFlagTriState:
    """--wire / --no-wire share a dest with default=None, and argparse's
    store_false action carries its own default of True — if that default ever
    won, every bare `gonzo chat` would silently attach a billed search tool.
    """

    @pytest.mark.parametrize(
        ("argv", "expected"),
        [
            (["chat"], None),
            (["chat", "--wire"], True),
            (["chat", "--no-wire"], False),
            (["write", "x"], None),
            (["write", "x", "--wire"], True),
            (["write", "x", "--no-wire"], False),
        ],
    )
    def test_flag_resolution(self, argv, expected):
        from gonzo.cli import build_parser

        assert build_parser().parse_args(argv).wire is expected


class TestTransferFactCheck:
    def test_flags_missing_numbers(self):
        source = "The budget rose to $4.3 million across 2019, affecting 87 staff."
        output = "They hauled it to $4.3 million. Eighty-seven people felt it."
        dropped = Transferrer._dropped(source, output)
        assert "2019" in dropped
        assert "87" in dropped

    def test_clean_when_every_number_survives(self):
        source = "It cost $47 at 4:15."
        output = "Forty-seven dollars — $47 — and the clock said 4:15."
        assert Transferrer._dropped(source, output) == []
