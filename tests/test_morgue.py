"""The Morgue: the research desk's data, prompt contract, and wiring.

The Morgue is the one surface allowed to present words as Thompson's, so the
tests pin three things hard: every quote in the bank is short, sourced, and
labeled; the prompt tells the model to quote from the bank and nowhere else;
and the identity rules match the Correspondent's.
"""

from unittest.mock import MagicMock

import pytest

from gonzo.config import MODES, wire_tool
from gonzo.modes.morgue import MorgueSession
from gonzo.research.morgue import (
    MORGUE_GUARDRAILS,
    QUOTE_WORD_LIMIT,
    STATUSES,
    load_fact_file,
    load_quote_bank,
    morgue_system_prompt,
    quote_word_count,
)
from gonzo.style.spec import load_spec


def _norm(text: str) -> str:
    return " ".join(text.split())


class TestQuoteBank:
    def test_bank_is_not_empty(self):
        assert len(load_quote_bank()) >= 20

    def test_every_quote_is_under_the_word_limit(self):
        """Short quotation for study, never a passage."""
        for q in load_quote_bank():
            assert quote_word_count(q.text) < QUOTE_WORD_LIMIT, (q.id, q.text)

    def test_word_count_ignores_dashes_and_ellipses(self):
        assert quote_word_count("We are At War now -- with somebody") == 7
        assert quote_word_count("I have stolen ... from the Book") == 6
        assert quote_word_count("one — two") == 2

    def test_every_status_is_known(self):
        for q in load_quote_bank():
            assert q.status in STATUSES, q.id

    def test_every_quote_has_an_https_source(self):
        for q in load_quote_bank():
            assert q.sources, q.id
            assert all(url.startswith("https://") for url in q.sources), q.id

    def test_ids_are_unique(self):
        ids = [q.id for q in load_quote_bank()]
        assert len(ids) == len(set(ids))

    def test_secondary_and_disputed_entries_explain_themselves(self):
        """A label without a reason teaches the user nothing."""
        for q in load_quote_bank():
            if q.status in {"secondary", "disputed"}:
                assert q.note.strip(), q.id

    def test_every_entry_names_its_work_and_date(self):
        for q in load_quote_bank():
            assert q.work.strip() and q.date.strip(), q.id

    def test_known_misattributions_are_labeled_disputed(self):
        """Regression guard for the research pass: these lines circulate as
        settled quotations and failed the check. They may stay in the bank
        only as teaching entries."""
        status = {q.id: q.status for q in load_quote_bank()}
        for qid in ("weird-turn-pro", "type-out-their-work", "too-weird-to-live"):
            assert status[qid] == "disputed", qid

    def test_bolero_carries_the_publishers_wording(self):
        """The ingested report misprints 'wilder' as 'wider'."""
        bolero = next(q for q in load_quote_bank() if q.id == "bolero")
        assert "wilder climax" in bolero.text
        assert "wider" not in bolero.text


class TestFactFile:
    def test_facts_are_sourced(self):
        for f in load_fact_file().facts:
            assert f.status in {"verified", "secondary"}, f.id
            assert f.sources and all(u.startswith("https://") for u in f.sources), f.id

    def test_known_conflicts_are_recorded_not_resolved_silently(self):
        conflicts = {f.id: f.conflict for f in load_fact_file().facts}
        for fid in ("born", "hells-angels", "sheriff", "hume-logan-letter"):
            assert conflicts[fid].strip(), fid

    def test_unverified_claims_carry_their_origin(self):
        unverified = load_fact_file().unverified
        assert unverified
        assert all(claim and origin for claim, origin in unverified)


class TestMorguePrompt:
    def test_guardrails_come_last(self):
        for wire in (False, True):
            prompt = morgue_system_prompt(wire=wire)
            assert prompt.rstrip().endswith(MORGUE_GUARDRAILS.strip())

    def test_is_byte_stable(self):
        """It sits in the cache prefix, like the style prompt."""
        morgue_system_prompt.cache_clear()
        first = morgue_system_prompt()
        morgue_system_prompt.cache_clear()
        assert morgue_system_prompt() == first

    def test_contains_no_hash_like_markers(self):
        import re

        assert not re.search(r"[0-9a-f]{32}", morgue_system_prompt())

    def test_identity_rules_match_the_correspondent(self):
        prompt = _norm(morgue_system_prompt())
        assert "You are not Hunter S. Thompson" in prompt
        assert "Never write under his byline" in prompt
        assert "Never fabricate biographical claims" in prompt
        assert "You may discuss him freely" in prompt
        assert "override every other instruction, including anything a user asks for" in prompt

    def test_quotation_is_bank_only(self):
        prompt = _norm(morgue_system_prompt())
        assert "Quote him only from the quote bank" in prompt
        assert "Never put quotation marks around words that are not in the bank" in prompt
        assert "Never reconstruct a quotation from memory" in prompt

    def test_every_bank_entry_reaches_the_model(self):
        prompt = morgue_system_prompt()
        for q in load_quote_bank():
            assert f'[{q.status}] "{q.text}"' in prompt, q.id

    def test_every_fact_reaches_the_model(self):
        prompt = morgue_system_prompt()
        for f in load_fact_file().facts:
            assert f.claim in prompt, f.id

    def test_disputed_entries_say_what_is_disputed(self):
        prompt = morgue_system_prompt()
        for q in load_quote_bank():
            if q.status == "disputed":
                assert f'"{q.text}"' in prompt
                line = next(ln for ln in prompt.splitlines() if f'"{q.text}"' in ln)
                assert "Dispute:" in line, q.id

    def test_wireless_variant_denies_the_wire(self):
        plain = _norm(morgue_system_prompt(wire=False))
        assert "you have no search" in plain
        assert "Never offer to look something up" in plain
        assert "The desk has installed a wire" not in plain

    def test_wire_variant_installs_it_and_keeps_the_quote_limit(self):
        """The limit must live in the wire section itself. Guardrail 2 also
        says 'fewer than 15 words', so a prompt-wide search could not see the
        wire section lose its own limit (a mutation proved it)."""
        from gonzo.research.morgue import MORGUE_WIRE_SECTION

        wired = _norm(morgue_system_prompt(wire=True))
        section = _norm(MORGUE_WIRE_SECTION)
        assert section in wired
        assert "The desk has installed a wire" in section
        assert "you have no search" not in wired
        assert "only in a single phrase of fewer than 15 words" in section
        assert "Never stitch searched phrases together" in section

    def test_death_is_handled_plainly_and_with_care(self):
        prompt = _norm(morgue_system_prompt())
        assert "without describing the method" in prompt
        assert "respond to them as a person first" in prompt

    def test_is_a_different_prompt_from_the_correspondent(self):
        """The two desks must never blend: the Correspondent forbids all
        quotation, the Morgue permits bank quotation only."""
        style = load_spec().system_prompt()
        morgue = morgue_system_prompt()
        assert "The quote bank" not in style
        assert "Stylistic assignment" not in morgue

    def test_long_enough_to_cache(self):
        assert len(morgue_system_prompt()) / 4 > 512


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.complete.return_value = MagicMock(text="a sourced answer", sources=[])
    return client


class TestMorgueSession:
    def test_uses_the_morgue_prompt_and_mode(self, fake_client):
        MorgueSession(client=fake_client).send("when was he born?")
        kwargs = fake_client.complete.call_args.kwargs
        assert kwargs["system_prompt"] == morgue_system_prompt(wire=False)
        assert kwargs["mode"] == "morgue"
        assert "morgue" in MODES

    def test_sends_no_style_directive(self, fake_client):
        """A research desk wants one careful register; the only per-turn
        content is the question."""
        MorgueSession(client=fake_client).send("when was he born?")
        last = fake_client.complete.call_args.kwargs["messages"][-1]
        assert last == {"role": "user", "content": "when was he born?"}
        assert "style_directive" not in fake_client.complete.call_args.kwargs["system_prompt"]

    def test_wire_pairs_tool_and_prompt(self, fake_client):
        MorgueSession(client=fake_client, wire=True).send("what changed in 2025?")
        kwargs = fake_client.complete.call_args.kwargs
        assert kwargs["tools"] == [wire_tool()]
        assert kwargs["system_prompt"] == morgue_system_prompt(wire=True)

    def test_wireless_sends_no_tools(self, fake_client):
        MorgueSession(client=fake_client).send("q")
        assert fake_client.complete.call_args.kwargs["tools"] is None

    def test_history_replays_and_resets(self, fake_client):
        session = MorgueSession(client=fake_client)
        session.send("first")
        session.send("second")
        sent = fake_client.complete.call_args.kwargs["messages"]
        assert sent[0] == {"role": "user", "content": "first"}
        assert sent[1] == {"role": "assistant", "content": "a sourced answer"}
        session.reset()
        assert session.history == []

    def test_stream_discards_text_before_a_reset(self, fake_client):
        from gonzo.client import STREAM_RESET

        fake_client.stream.return_value = iter(["abandoned", STREAM_RESET, "kept"])
        session = MorgueSession(client=fake_client)
        list(session.stream("q"))
        assert session.history[1] == {"role": "assistant", "content": "kept"}


class TestMorgueSurfaces:
    def test_cli_has_the_command(self):
        from gonzo.cli import build_parser

        args = build_parser().parse_args(["morgue", "when", "was", "he", "born?"])
        assert args.question == ["when", "was", "he", "born?"]
        assert args.func.__name__ == "cmd_morgue"

    def test_server_streams_a_morgue_turn(self):
        from fastapi.testclient import TestClient

        import gonzo.server as server

        with TestClient(server.app) as web:
            first = web.post("/api/morgue", json={"message": "hi"})
            session_id = first.text.split('"session_id": "')[1].split('"')[0]
            session = server._MORGUE_SESSIONS[session_id]
            session.stream = lambda _message: iter(["from the record"])
            body = web.post(
                "/api/morgue", json={"message": "born?", "session_id": session_id}
            ).text
        assert "from the record" in body
        assert "event: done" in body

    def test_chat_ids_do_not_resolve_in_the_morgue(self):
        """A chat id must never replay a correspondent's history into the
        research desk, and must not overwrite the chat session's lock."""
        from fastapi.testclient import TestClient

        import gonzo.server as server

        with TestClient(server.app) as web:
            chat = web.post("/api/chat", json={"message": "hi"})
            chat_id = chat.text.split('"session_id": "')[1].split('"')[0]
            chat_lock = server._SESSION_LOCKS[chat_id]
            morgue = web.post("/api/morgue", json={"message": "hi", "session_id": chat_id})
            morgue_id = morgue.text.split('"session_id": "')[1].split('"')[0]
        assert morgue_id != chat_id
        assert server._SESSION_LOCKS[chat_id] is chat_lock

    def test_health_reports_the_bank(self):
        from fastapi.testclient import TestClient

        import gonzo.server as server

        with TestClient(server.app) as web:
            data = web.get("/api/health").json()
        assert data["morgue"]["quote_bank"] == len(load_quote_bank())
