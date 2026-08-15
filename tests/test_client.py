"""Request construction.

Verifies the wire shape without an API key. This catches the failures that would
otherwise only appear as a 400 in production, and it pins the two constraints
the whole architecture is built around: no sampling parameters on Opus 5, and a
cache breakpoint on a byte-stable system prefix.
"""

from unittest.mock import MagicMock, patch

import pytest

from gonzo.client import (
    Completion,
    GonzoClient,
    RefusalError,
    Usage,
    WireSearch,
    WireSource,
    WireSources,
)
from gonzo.config import (
    FALLBACK_BETA,
    MODEL,
    WIRE_MAX_CONTINUATIONS,
    WIRE_MAX_SEARCHES,
    WIRE_TOOL_VERSION,
    wire_tool,
)


def _text_block(text, citations=None):
    block = MagicMock()
    block.type = "text"
    block.text = text
    block.citations = citations
    return block


def _citation(url, title=""):
    cit = MagicMock()
    cit.type = "web_search_result_location"
    cit.url = url
    cit.title = title
    return cit


def _fake_response(text="output", stop_reason="end_turn", content=None, searches=0, **usage):
    response = MagicMock()
    response.content = [_text_block(text)] if content is None else content
    response.stop_reason = stop_reason
    response.model = MODEL
    response.usage = MagicMock(
        input_tokens=usage.get("input_tokens", 100),
        output_tokens=usage.get("output_tokens", 50),
        cache_read_input_tokens=usage.get("cache_read", 0),
        cache_creation_input_tokens=usage.get("cache_write", 0),
    )
    response.usage.server_tool_use = MagicMock(web_search_requests=searches)
    return response


@pytest.fixture
def client_and_calls():
    """Mocks the streaming path, which is what `complete()` actually uses.

    `complete()` streams internally: the SDK rejects non-streaming requests
    whose max_tokens could exceed ten minutes, and compose mode budgets 32k.
    """
    with patch("anthropic.Anthropic") as ctor:
        sdk = MagicMock()
        ctor.return_value = sdk

        context = MagicMock()
        context.__enter__.return_value.get_final_message.return_value = _fake_response()
        context.__exit__.return_value = False
        sdk.beta.messages.stream.return_value = context

        yield GonzoClient(api_key="test-key"), sdk.beta.messages.stream


def _kwargs(client, calls, **over):
    client.complete(
        system_prompt=over.pop("system_prompt", "SPEC"),
        messages=over.pop("messages", [{"role": "user", "content": "hello"}]),
        **over,
    )
    return calls.call_args.kwargs


class TestNoSamplingParameters:
    """`temperature`/`top_p`/`top_k` are rejected with a 400 on Opus 5.

    This is the constraint that forced the Variance Director into existence, so
    a regression here breaks every request the engine makes.
    """

    @pytest.mark.parametrize("param", ["temperature", "top_p", "top_k"])
    def test_never_sent(self, client_and_calls, param):
        assert param not in _kwargs(*client_and_calls)


class TestPromptCaching:
    def test_system_carries_a_cache_breakpoint(self, client_and_calls):
        system = _kwargs(*client_and_calls)["system"]
        assert system[-1]["cache_control"] == {"type": "ephemeral"}

    def test_system_holds_the_spec_only(self, client_and_calls):
        """Everything volatile belongs in `messages`, after the breakpoint."""
        kwargs = _kwargs(*client_and_calls)
        assert kwargs["system"][0]["text"] == "SPEC"
        assert len(kwargs["system"]) == 1

    def test_identical_prefix_across_calls(self, client_and_calls):
        client, calls = client_and_calls
        first = _kwargs(client, calls)["system"]
        second = _kwargs(client, calls, messages=[{"role": "user", "content": "different"}])["system"]
        assert first == second, "system prefix drifted; caching would break"


class TestRequestShape:
    def test_uses_configured_model(self, client_and_calls):
        assert _kwargs(*client_and_calls)["model"] == MODEL

    def test_effort_is_nested_in_output_config(self, client_and_calls):
        """`effort` is not a top-level parameter."""
        kwargs = _kwargs(*client_and_calls)
        assert kwargs["output_config"]["effort"] == "medium"
        assert "effort" not in kwargs

    def test_mode_selects_effort_and_budget(self, client_and_calls):
        kwargs = _kwargs(*client_and_calls, mode="compose")
        assert kwargs["output_config"]["effort"] == "high"
        assert kwargs["max_tokens"] == 32_000

    def test_opts_into_refusal_fallbacks(self, client_and_calls):
        kwargs = _kwargs(*client_and_calls)
        assert kwargs["fallbacks"] == "default"
        assert FALLBACK_BETA in kwargs["betas"]


class TestRefusalHandling:
    def test_refusal_raises_rather_than_returning_empty(self, client_and_calls):
        """A decline is HTTP 200 with an empty content list. Reading content[0]
        unconditionally would crash; returning "" would be a silent wrong answer."""
        client, calls = client_and_calls
        refusal = _fake_response(stop_reason="refusal")
        refusal.content = []
        refusal.stop_details = MagicMock(category="cyber", explanation="declined")
        calls.return_value.__enter__.return_value.get_final_message.return_value = refusal

        with pytest.raises(RefusalError) as excinfo:
            client.complete(system_prompt="SPEC", messages=[{"role": "user", "content": "x"}])
        assert excinfo.value.category == "cyber"

    def test_normal_response_returns_text(self, client_and_calls):
        client, calls = client_and_calls
        calls.return_value.__enter__.return_value.get_final_message.return_value = _fake_response(
            text="  the piece  "
        )
        assert client.complete(
            system_prompt="SPEC", messages=[{"role": "user", "content": "x"}]
        ).text == "the piece"


class TestUsage:
    def test_reports_cache_reads(self):
        """cache_read > 0 on turn 2+ is the proof caching is actually live."""
        usage = Usage.from_response(
            MagicMock(
                input_tokens=10, output_tokens=20,
                cache_read_input_tokens=2900, cache_creation_input_tokens=0,
            )
        )
        assert usage.cache_read == 2900
        assert "cache_read=2900" in str(usage)

    def test_completion_defaults(self):
        assert Completion(text="x").usage.input_tokens == 0


class TestWireRequestShape:
    def test_tool_entry_matches_the_api_contract(self):
        assert wire_tool() == {
            "type": WIRE_TOOL_VERSION,
            "name": "web_search",
            "max_uses": WIRE_MAX_SEARCHES,
        }

    def test_no_tools_key_without_the_wire(self, client_and_calls):
        """A wireless request must be byte-identical to the pre-wire engine —
        `tools` sits before `system` in the cache prefix, so even an empty
        list would fork the cache for every existing user."""
        assert "tools" not in _kwargs(*client_and_calls)

    def test_tools_passed_through_verbatim(self, client_and_calls):
        client, calls = client_and_calls
        client.complete(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "x"}],
            tools=[wire_tool()],
        )
        assert calls.call_args.kwargs["tools"] == [wire_tool()]


class TestPauseTurn:
    """A searched turn can stop with `pause_turn`: a half-finished turn the
    API expects back verbatim as an assistant message, same tools, to resume.
    The rounds concatenate into one logical reply."""

    def _paused_then_done(self, calls):
        stu = MagicMock()
        stu.type = "server_tool_use"
        stu.name = "web_search"
        paused = _fake_response(
            stop_reason="pause_turn",
            content=[_text_block("Checking the record. "), stu],
            searches=1,
        )
        done = _fake_response(
            content=[_text_block(
                "The board voted 4-1.",
                citations=[_citation("https://apnews.com/x", "AP")],
            )],
        )
        calls.return_value.__enter__.return_value.get_final_message.side_effect = [paused, done]
        return paused

    def test_resumes_with_content_passed_back_verbatim(self, client_and_calls):
        client, calls = client_and_calls
        paused = self._paused_then_done(calls)

        result = client.complete(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "what happened"}],
            tools=[wire_tool()],
        )

        assert calls.call_count == 2
        resumed = calls.call_args.kwargs["messages"]
        assert resumed[-1] == {"role": "assistant", "content": paused.content}
        assert calls.call_args.kwargs["tools"] == [wire_tool()], "resume must keep the tools"
        assert result.text == "Checking the record. The board voted 4-1."

    def test_collects_sources_and_searches_across_rounds(self, client_and_calls):
        client, calls = client_and_calls
        self._paused_then_done(calls)

        result = client.complete(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "what happened"}],
            tools=[wire_tool()],
        )

        assert result.sources == [WireSource(url="https://apnews.com/x", title="AP")]
        assert result.usage.searches == 1
        assert result.usage.output_tokens == 100, "usage must sum across rounds"

    def test_resume_loop_is_bounded(self, client_and_calls):
        """A metered tool plus an unbounded retry loop is an invoice, not a
        bug. After the cap the engine returns what stands."""
        client, calls = client_and_calls
        forever_paused = [
            _fake_response(stop_reason="pause_turn", text="still going. ")
            for _ in range(WIRE_MAX_CONTINUATIONS + 5)
        ]
        calls.return_value.__enter__.return_value.get_final_message.side_effect = forever_paused

        result = client.complete(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "x"}],
            tools=[wire_tool()],
        )

        assert calls.call_count == WIRE_MAX_CONTINUATIONS + 1
        assert "still going." in result.text

    def test_wireless_completion_reports_no_sources(self, client_and_calls):
        client, _ = client_and_calls
        result = client.complete(
            system_prompt="SPEC", messages=[{"role": "user", "content": "x"}]
        )
        assert result.sources == []


class TestWireStream:
    def _events(self):
        def ev(**attrs):
            event = MagicMock()
            for key, value in attrs.items():
                setattr(event, key, value)
            return event

        search_block = MagicMock()
        search_block.type = "server_tool_use"
        search_block.name = "web_search"

        return [
            ev(type="content_block_start", index=0, content_block=search_block),
            ev(type="content_block_delta", index=0,
               delta=ev(type="input_json_delta", partial_json='{"query": "county wat')),
            ev(type="content_block_delta", index=0,
               delta=ev(type="input_json_delta", partial_json='er board 2026"}')),
            ev(type="content_block_stop", index=0),
            ev(type="content_block_delta", index=2,
               delta=ev(type="text_delta", text="The board voted 4-1.")),
        ]

    def test_stream_yields_search_then_text_then_sources(self, client_and_calls):
        client, calls = client_and_calls
        inner = calls.return_value.__enter__.return_value
        inner.__iter__.return_value = iter(self._events())
        inner.get_final_message.return_value = _fake_response(
            content=[_text_block(
                "The board voted 4-1.",
                citations=[_citation("https://apnews.com/x", "AP")],
            )],
            searches=1,
        )

        got = list(client.stream(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "x"}],
            tools=[wire_tool()],
        ))

        assert got[0] == WireSearch(query="county water board 2026"), (
            "the query is streamed as partial JSON and must be reassembled"
        )
        assert got[1] == "The board voted 4-1."
        assert got[-1] == WireSources(
            sources=(WireSource(url="https://apnews.com/x", title="AP"),)
        )

    def test_wireless_stream_emits_no_wire_events(self, client_and_calls):
        """Without tools the stream contract is unchanged: text and resets
        only. Existing callers must not need to learn new event types."""
        client, calls = client_and_calls
        inner = calls.return_value.__enter__.return_value
        inner.__iter__.return_value = iter([])
        inner.get_final_message.return_value = _fake_response()

        got = list(client.stream(
            system_prompt="SPEC", messages=[{"role": "user", "content": "x"}]
        ))
        assert not any(isinstance(g, (WireSearch, WireSources)) for g in got)

    def test_stream_resumes_a_paused_turn_seamlessly(self, client_and_calls):
        """`pause_turn` mid-stream must continue, not end: the caller sees one
        uninterrupted stream — no reset, both rounds' text, one sources event
        covering everything cited across rounds."""
        client, calls = client_and_calls

        def ev(**attrs):
            event = MagicMock()
            for key, value in attrs.items():
                setattr(event, key, value)
            return event

        round_one = [ev(type="content_block_delta", index=0,
                        delta=ev(type="text_delta", text="Checking. "))]
        round_two = [ev(type="content_block_delta", index=0,
                        delta=ev(type="text_delta", text="The vote was 4-1."))]
        paused = _fake_response(stop_reason="pause_turn", text="Checking. ")
        done = _fake_response(content=[_text_block(
            "The vote was 4-1.", citations=[_citation("https://apnews.com/x", "AP")]
        )])

        inner = calls.return_value.__enter__.return_value
        inner.__iter__.side_effect = [iter(round_one), iter(round_two)]
        inner.get_final_message.side_effect = [paused, done]

        got = list(client.stream(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "x"}],
            tools=[wire_tool()],
        ))

        assert calls.call_count == 2, "a paused stream must resume, not stop"
        resumed = calls.call_args.kwargs["messages"]
        assert resumed[-1] == {"role": "assistant", "content": paused.content}
        assert [g for g in got if isinstance(g, str)] == ["Checking. ", "The vote was 4-1."]
        assert len(got) == 3, "text, text, sources — and nothing else, no reset"
        assert got[-1] == WireSources(
            sources=(WireSource(url="https://apnews.com/x", title="AP"),)
        )

    def test_duplicate_urls_deduplicate(self, client_and_calls):
        client, calls = client_and_calls
        inner = calls.return_value.__enter__.return_value
        inner.__iter__.return_value = iter([])
        inner.get_final_message.return_value = _fake_response(
            content=[
                _text_block("One. ", citations=[_citation("https://a.example/1", "A")]),
                _text_block("Two.", citations=[
                    _citation("https://a.example/1", "A"),
                    _citation("https://b.example/2", "B"),
                ]),
            ],
        )

        got = list(client.stream(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "x"}],
            tools=[wire_tool()],
        ))
        assert got[-1].sources == (
            WireSource(url="https://a.example/1", title="A"),
            WireSource(url="https://b.example/2", title="B"),
        )


class TestLongOutputTimeout:
    """Regression: compose budgets 32k tokens.

    A plain `messages.create()` at that size raises
    `ValueError: Streaming is required for operations that may take longer than
    10 minutes` before the request is even sent -- it broke every `gonzo write`
    invocation. `complete()` streams internally so the guard never trips.
    """

    def test_complete_uses_the_streaming_endpoint(self, client_and_calls):
        client, stream_calls = client_and_calls
        client.complete(
            system_prompt="SPEC",
            messages=[{"role": "user", "content": "x"}],
            mode="compose",
        )
        assert stream_calls.called, "complete() must stream, not call create()"
        assert stream_calls.call_args.kwargs["max_tokens"] == 32_000
