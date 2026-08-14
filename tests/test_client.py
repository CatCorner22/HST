"""Request construction.

Verifies the wire shape without an API key. This catches the failures that would
otherwise only appear as a 400 in production, and it pins the two constraints
the whole architecture is built around: no sampling parameters on Opus 5, and a
cache breakpoint on a byte-stable system prefix.
"""

from unittest.mock import MagicMock, patch

import pytest

from gonzo.client import Completion, GonzoClient, RefusalError, Usage
from gonzo.config import FALLBACK_BETA, MODEL


def _fake_response(text="output", stop_reason="end_turn", **usage):
    block = MagicMock()
    block.type = "text"
    block.text = text

    response = MagicMock()
    response.content = [block]
    response.stop_reason = stop_reason
    response.model = MODEL
    response.usage = MagicMock(
        input_tokens=usage.get("input_tokens", 100),
        output_tokens=usage.get("output_tokens", 50),
        cache_read_input_tokens=usage.get("cache_read", 0),
        cache_creation_input_tokens=usage.get("cache_write", 0),
    )
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
