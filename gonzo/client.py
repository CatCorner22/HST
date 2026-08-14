"""Anthropic API wrapper: cached prompts, streaming, refusal handling.

Three things this centralizes:

1. **The cache breakpoint.** The style spec is ~3k tokens and byte-identical on
   every request. It goes in `system` with `cache_control: ephemeral`, so from
   the second turn onward it bills at roughly a tenth of input price. Everything
   volatile (the variance directive, the conversation) goes in `messages`, after
   the breakpoint.

2. **Refusals.** Opus 5 ships elevated safety classifiers. A declined request
   returns HTTP 200 with `stop_reason == "refusal"` and possibly an empty
   `content` list — code that reads `content[0]` unconditionally crashes on it.
   Every read here checks `stop_reason` first, and requests opt into server-side
   fallbacks so a decline is re-served rather than lost.

3. **No sampling parameters.** `temperature`/`top_p`/`top_k` are rejected with a
   400 on Opus 5. Variance comes from `gonzo.style.variance` instead.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import ValidationError

from gonzo.config import FALLBACK_BETA, MODEL, MODES, resolve_api_key

log = logging.getLogger(__name__)


class CredentialsError(RuntimeError):
    """No usable Anthropic credentials.

    The SDK raises a bare TypeError deep in header validation for this, which
    is the single most common setup failure and reads like a bug in the engine.
    """

    MESSAGE = (
        "No Anthropic credentials found. Either:\n"
        "  - export ANTHROPIC_API_KEY=sk-ant-...   (see .env.example)\n"
        "  - or run `ant auth login`, which stores a profile the SDK reads automatically."
    )

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


class StreamReset:
    """Sentinel yielded by `stream()` when a fallback supersedes prior output.

    Everything yielded before it came from a model that subsequently declined.
    Callers should discard it and render only what follows.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "<StreamReset>"


STREAM_RESET = StreamReset()


class StructuredOutputError(RuntimeError):
    """The model's reply did not satisfy the requested schema.

    Usually a decline (prose where JSON was demanded) or a truncated response.
    Raised instead of a bare pydantic ValidationError so callers can catch one
    engine-level type.
    """

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(f"model output did not match the requested schema: {cause}")


class EmptyCompletionError(RuntimeError):
    """The model returned no prose -- thinking-only, or truncated at max_tokens."""

    def __init__(self, stop_reason: str | None) -> None:
        self.stop_reason = stop_reason
        hint = (
            " The response hit max_tokens before producing text; raise max_tokens."
            if stop_reason == "max_tokens"
            else ""
        )
        super().__init__(f"model returned no text (stop_reason={stop_reason}).{hint}")


class RefusalError(RuntimeError):
    """The request was declined by safety classifiers, fallbacks included."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        detail = explanation or "no explanation provided"
        super().__init__(f"request declined (category={category or 'unknown'}): {detail}")


@dataclass
class Usage:
    """Token accounting for one call. `cache_read` proves caching is live."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0

    @classmethod
    def from_response(cls, usage: Any) -> "Usage":
        return cls(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    def __str__(self) -> str:
        return (
            f"in={self.input_tokens} out={self.output_tokens} "
            f"cache_read={self.cache_read} cache_write={self.cache_write}"
        )


@dataclass
class Completion:
    """A finished generation."""

    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = MODEL


def _system_blocks(system_prompt: str) -> list[dict[str, Any]]:
    """The system prefix with a cache breakpoint on its final block."""
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _credentials_error(exc: TypeError) -> Exception:
    """Translate the SDK's auth TypeError; re-raise anything else untouched."""
    if "authentication method" in str(exc):
        return CredentialsError()
    return exc


def _check_refusal(response: Any) -> None:
    if getattr(response, "stop_reason", None) != "refusal":
        return
    details = getattr(response, "stop_details", None)
    raise RefusalError(
        getattr(details, "category", None),
        getattr(details, "explanation", None),
    )


def _text_of(response: Any) -> str:
    """Concatenate the text blocks belonging to the model that actually answered.

    Every request opts into server-side fallbacks, so when one fires the API
    returns a single message whose `content` is

        [<declining model's abandoned blocks>, fallback, <replacement's blocks>]

    with `stop_reason == "end_turn"` -- a normal success. Concatenating every
    text block would glue the refused half-sentence onto the replacement's
    fresh opening and hand it back as a clean result. One `fallback` block
    appears per hop that ran and declined, so text after the *last* one is the
    reply that stands.
    """
    blocks = list(getattr(response, "content", None) or [])
    last_hop = max(
        (i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"),
        default=-1,
    )
    return "".join(
        b.text for b in blocks[last_hop + 1 :] if getattr(b, "type", None) == "text"
    ).strip()


class GonzoClient:
    """Thin wrapper over the Anthropic SDK for this engine's call shapes."""

    def __init__(self, api_key: str | None = None, model: str = MODEL) -> None:
        key = api_key or resolve_api_key()
        # A zero-arg client is deliberate when no key is set: the SDK also
        # resolves ANTHROPIC_AUTH_TOKEN and `ant auth login` profiles on disk.
        self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        self.model = model

    # -- shared request construction ---------------------------------------

    def _request_kwargs(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        mode: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        cfg = MODES[mode]
        return {
            "model": model or self.model,
            "max_tokens": cfg.max_tokens,
            "system": _system_blocks(system_prompt),
            "messages": messages,
            "output_config": {"effort": cfg.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }

    # -- generation --------------------------------------------------------

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        mode: str = "chat",
        model: str | None = None,
    ) -> Completion:
        """Generate and return the whole result.

        Streams under the hood even though callers want one string. The SDK
        refuses a non-streaming request whose `max_tokens` it estimates could
        run past ten minutes -- and compose mode budgets 32k tokens, so the
        obvious `messages.create()` raises `ValueError` before sending
        anything. Streaming and accumulating sidesteps the timeout guard
        entirely and costs the caller nothing.
        """
        kwargs = self._request_kwargs(
            system_prompt=system_prompt, messages=messages, mode=mode, model=model
        )
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()
        except TypeError as exc:
            raise _credentials_error(exc) from exc

        _check_refusal(response)

        text = _text_of(response)
        if not text:
            # A thinking-only or truncated response yields no prose. Returning
            # it as a success would write an empty assistant turn into history
            # and score an empty string, both silently.
            raise EmptyCompletionError(getattr(response, "stop_reason", None))

        return Completion(
            text=text,
            usage=Usage.from_response(response.usage),
            model=getattr(response, "model", self.model),
        )

    def stream(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        mode: str = "chat",
        model: str | None = None,
    ) -> Iterator[str | StreamReset]:
        """Streaming generation, yielding text deltas.

        Iterates typed events rather than `text_stream` so hop boundaries stay
        visible. When a server-side fallback fires mid-stream, everything
        already yielded came from the model that then declined -- abandoned
        text, not part of the answer. There is no way to un-send it, so the
        generator yields `STREAM_RESET` at the boundary and callers discard
        what they have shown. A caller that ignores the sentinel degrades to
        the old spliced behavior rather than crashing.

        The final message is inspected after the stream drains, so a refusal
        still raises instead of returning a silent partial.
        """
        kwargs = self._request_kwargs(
            system_prompt=system_prompt, messages=messages, mode=mode, model=model
        )
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                for event in stream:
                    kind = getattr(event, "type", None)
                    if kind == "content_block_start":
                        if getattr(event.content_block, "type", None) == "fallback":
                            yield STREAM_RESET
                    elif kind == "content_block_delta":
                        if getattr(event.delta, "type", None) == "text_delta":
                            yield event.delta.text
                final = stream.get_final_message()
        except TypeError as exc:
            raise _credentials_error(exc) from exc

        _check_refusal(final)
        log.debug("stream usage: %s", Usage.from_response(final.usage))

    def parse(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        schema: type,
        mode: str = "judge",
        model: str | None = None,
    ) -> Any:
        """Structured generation validated against a Pydantic model."""
        cfg = MODES[mode]
        try:
            response = self._client.messages.parse(
                model=model or self.model,
                max_tokens=cfg.max_tokens,
                system=_system_blocks(system_prompt),
                messages=messages,
                output_config={"effort": cfg.effort},
                output_format=schema,
            )
        except TypeError as exc:
            raise _credentials_error(exc) from exc
        except ValidationError as exc:
            # A decline returns prose where JSON was demanded, so pydantic
            # raises before we ever get to look at stop_reason. Surface it as
            # something callers already handle rather than a schema error.
            raise StructuredOutputError(exc) from exc

        _check_refusal(response)
        return response.parsed_output
