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
    """Concatenate text blocks, skipping thinking and other block types."""
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
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
        return Completion(
            text=_text_of(response),
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
    ) -> Iterator[str]:
        """Streaming generation, yielding text deltas.

        The final message is inspected after the stream drains so a mid-stream
        refusal still raises rather than returning a silent partial.
        """
        kwargs = self._request_kwargs(
            system_prompt=system_prompt, messages=messages, mode=mode, model=model
        )
        try:
            with self._client.beta.messages.stream(**kwargs) as stream:
                for chunk in stream.text_stream:
                    yield chunk
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
        _check_refusal(response)
        return response.parsed_output
