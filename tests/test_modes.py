"""Mode wiring: where the directive goes, and what history keeps."""

from unittest.mock import MagicMock

import pytest

from gonzo.modes.chat import ChatSession
from gonzo.modes.transfer import Transferrer


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
