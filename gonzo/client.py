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

The wire (server-side web search) adds two call shapes on top: a turn can
stop with `pause_turn` and expect its partial content back to resume, and a
searched reply carries citations that are surfaced as `WireSource` records.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import anthropic
from pydantic import ValidationError

from gonzo.config import (
    FALLBACK_BETA,
    MODEL,
    MODES,
    WIRE_MAX_CONTINUATIONS,
    resolve_api_key,
)

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


@dataclass(frozen=True)
class WireSource:
    """One source the model actually cited from a web search."""

    url: str
    title: str

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title}


@dataclass(frozen=True)
class WireSearch:
    """Yielded by `stream()` when the model pulls the wire.

    Emitted once the search query is fully formed, before results return, so
    a UI can show what is being checked while the search runs. `query` is ""
    when the streamed tool input could not be parsed — treat that as "a
    search is happening", not an error.
    """

    query: str


@dataclass(frozen=True)
class WireSources:
    """Yielded once at the end of a wire-enabled stream: every source cited.

    Emitted only when the request carried the web search tool. An empty list
    is meaningful — the wire was available and the reply cited nothing.
    """

    sources: tuple[WireSource, ...]


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
    searches: int = 0

    @staticmethod
    def _int(value: Any) -> int:
        # Search counts ride under usage.server_tool_use, which is absent on
        # wireless responses (and a Mock in tests) — count only a real int.
        return value if isinstance(value, int) else 0

    @classmethod
    def from_response(cls, usage: Any) -> "Usage":
        server_tools = getattr(usage, "server_tool_use", None)
        return cls(
            input_tokens=cls._int(getattr(usage, "input_tokens", 0)),
            output_tokens=cls._int(getattr(usage, "output_tokens", 0)),
            cache_read=cls._int(getattr(usage, "cache_read_input_tokens", 0)),
            cache_write=cls._int(getattr(usage, "cache_creation_input_tokens", 0)),
            searches=cls._int(getattr(server_tools, "web_search_requests", 0)),
        )

    def add(self, other: "Usage") -> None:
        """Fold another round's usage in — a paused turn bills per round."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read += other.cache_read
        self.cache_write += other.cache_write
        self.searches += other.searches

    def __str__(self) -> str:
        wire = f" searches={self.searches}" if self.searches else ""
        return (
            f"in={self.input_tokens} out={self.output_tokens} "
            f"cache_read={self.cache_read} cache_write={self.cache_write}{wire}"
        )


@dataclass
class Completion:
    """A finished generation."""

    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = MODEL
    sources: list[WireSource] = field(default_factory=list)


def _system_blocks(system_prompt: str) -> list[dict[str, Any]]:
    """The system prefix with a cache breakpoint on its final block."""
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _parse_query(fragments: list[str]) -> str:
    """Reassemble a search query from streamed partial-JSON fragments.

    Empty or unparseable input is not an error -- the search still ran
    server-side; the UI just cannot show what it asked.
    """
    raw = "".join(fragments).strip()
    if not raw:
        return ""
    try:
        query = json.loads(raw).get("query", "")
    except (json.JSONDecodeError, AttributeError):
        return ""
    return query if isinstance(query, str) else ""


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


def _standing_blocks(blocks: list[Any]) -> list[Any]:
    """The content blocks belonging to the model that actually answered.

    Every request opts into server-side fallbacks, so when one fires the API
    returns a single message whose `content` is

        [<declining model's abandoned blocks>, fallback, <replacement's blocks>]

    with `stop_reason == "end_turn"` -- a normal success. One `fallback` block
    appears per hop that ran and declined, so everything after the *last* one
    is the reply that stands.
    """
    last_hop = max(
        (i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"),
        default=-1,
    )
    return blocks[last_hop + 1 :]


def _text_of_blocks(blocks: list[Any]) -> str:
    """Concatenate the standing text. Skips the wire's tool-use and result
    blocks, which interleave with text on a searched turn -- concatenating
    everything would splice a refused half-sentence (or raw search plumbing)
    into the reply."""
    return "".join(
        b.text for b in _standing_blocks(blocks) if getattr(b, "type", None) == "text"
    ).strip()


def _text_of(response: Any) -> str:
    return _text_of_blocks(list(getattr(response, "content", None) or []))


def _sources_of_blocks(blocks: list[Any]) -> list[WireSource]:
    """Every web source the standing reply actually cited, in first-citation
    order, deduplicated by URL. Citations ride on text blocks; the raw
    `web_search_tool_result` blocks list everything a search *returned*, which
    is not the same thing as what the reply *used* -- only citations prove
    use, so only citations count."""
    seen: dict[str, WireSource] = {}
    for block in _standing_blocks(blocks):
        if getattr(block, "type", None) != "text":
            continue
        for citation in getattr(block, "citations", None) or []:
            if getattr(citation, "type", None) != "web_search_result_location":
                continue
            url = getattr(citation, "url", None)
            if not url or url in seen:
                continue
            seen[url] = WireSource(url=url, title=getattr(citation, "title", "") or "")
    return list(seen.values())


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
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        cfg = MODES[mode]
        kwargs = {
            "model": model or self.model,
            "max_tokens": cfg.max_tokens,
            "system": _system_blocks(system_prompt),
            "messages": messages,
            "output_config": {"effort": cfg.effort},
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
        if tools:
            # Tools sit before `system` in the cache prefix, so a request with
            # the wire and one without hold separate cache entries. Callers keep
            # the flag stable per session; leaving the key out entirely when
            # unused keeps wireless requests byte-identical to the pre-wire
            # engine.
            kwargs["tools"] = tools
        return kwargs

    # -- generation --------------------------------------------------------

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        mode: str = "chat",
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """Generate and return the whole result.

        Streams under the hood even though callers want one string. The SDK
        refuses a non-streaming request whose `max_tokens` it estimates could
        run past ten minutes -- and compose mode budgets 32k tokens, so the
        obvious `messages.create()` raises `ValueError` before sending
        anything. Streaming and accumulating sidesteps the timeout guard
        entirely and costs the caller nothing.

        A turn that pulls the wire can come back with `stop_reason ==
        "pause_turn"`: the API handing back a half-finished turn to resume.
        The recipe is to append the partial content verbatim as an assistant
        message and re-send with the same tools; the rounds concatenate into
        one logical reply, so text and citations are gathered across all of
        them. Bounded, because an unbounded resume loop with a metered tool
        is how a bug becomes an invoice.
        """
        convo = list(messages)
        blocks: list[Any] = []
        usage = Usage()
        response: Any = None

        for _ in range(WIRE_MAX_CONTINUATIONS + 1):
            kwargs = self._request_kwargs(
                system_prompt=system_prompt, messages=convo, mode=mode,
                model=model, tools=tools,
            )
            try:
                with self._client.beta.messages.stream(**kwargs) as stream:
                    response = stream.get_final_message()
            except TypeError as exc:
                raise _credentials_error(exc) from exc

            _check_refusal(response)
            blocks.extend(getattr(response, "content", None) or [])
            usage.add(Usage.from_response(response.usage))

            paused_content = getattr(response, "content", None)
            if getattr(response, "stop_reason", None) != "pause_turn" or not paused_content:
                break
            convo.append({"role": "assistant", "content": paused_content})
        else:
            log.warning(
                "turn still paused after %d continuations; returning what stands",
                WIRE_MAX_CONTINUATIONS,
            )

        text = _text_of_blocks(blocks)
        if not text:
            # A thinking-only or truncated response yields no prose. Returning
            # it as a success would write an empty assistant turn into history
            # and score an empty string, both silently.
            raise EmptyCompletionError(getattr(response, "stop_reason", None))

        return Completion(
            text=text,
            usage=usage,
            model=getattr(response, "model", self.model),
            sources=_sources_of_blocks(blocks) if tools else [],
        )

    def stream(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        mode: str = "chat",
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[str | StreamReset | WireSearch | WireSources]:
        """Streaming generation, yielding text deltas.

        Iterates typed events rather than `text_stream` so hop boundaries stay
        visible. When a server-side fallback fires mid-stream, everything
        already yielded came from the model that then declined -- abandoned
        text, not part of the answer. There is no way to un-send it, so the
        generator yields `STREAM_RESET` at the boundary and callers discard
        what they have shown. A caller that ignores the sentinel degrades to
        the old spliced behavior rather than crashing.

        With the wire attached, two more event kinds appear: a `WireSearch`
        as each search query finishes forming (the query streams in as
        partial JSON, so it is parsed at the block boundary), and one
        `WireSources` after the turn completes, carrying every source the
        reply cited. A `pause_turn` stop is resumed transparently -- the
        continuation rounds read as one uninterrupted stream, no reset.

        The final message is inspected after each round drains, so a refusal
        still raises instead of returning a silent partial.
        """
        convo = list(messages)
        blocks: list[Any] = []
        usage = Usage()

        for _ in range(WIRE_MAX_CONTINUATIONS + 1):
            kwargs = self._request_kwargs(
                system_prompt=system_prompt, messages=convo, mode=mode,
                model=model, tools=tools,
            )
            # Partial tool-input JSON per in-flight search block, keyed by
            # content index. Reset per round: indices restart in each round.
            pending: dict[int, list[str]] = {}
            try:
                with self._client.beta.messages.stream(**kwargs) as stream:
                    for event in stream:
                        kind = getattr(event, "type", None)
                        if kind == "content_block_start":
                            block = event.content_block
                            if getattr(block, "type", None) == "fallback":
                                yield STREAM_RESET
                            elif (
                                getattr(block, "type", None) == "server_tool_use"
                                and getattr(block, "name", None) == "web_search"
                            ):
                                pending[event.index] = []
                        elif kind == "content_block_delta":
                            delta = event.delta
                            if getattr(delta, "type", None) == "text_delta":
                                yield delta.text
                            elif (
                                getattr(delta, "type", None) == "input_json_delta"
                                and event.index in pending
                            ):
                                pending[event.index].append(delta.partial_json)
                        elif kind == "content_block_stop" and getattr(event, "index", None) in pending:
                            yield WireSearch(query=_parse_query(pending.pop(event.index)))
                    final = stream.get_final_message()
            except TypeError as exc:
                raise _credentials_error(exc) from exc

            _check_refusal(final)
            blocks.extend(getattr(final, "content", None) or [])
            usage.add(Usage.from_response(final.usage))

            paused_content = getattr(final, "content", None)
            if getattr(final, "stop_reason", None) != "pause_turn" or not paused_content:
                break
            convo.append({"role": "assistant", "content": paused_content})
        else:
            log.warning(
                "stream still paused after %d continuations; ending with what stands",
                WIRE_MAX_CONTINUATIONS,
            )

        log.debug("stream usage: %s", usage)
        if tools:
            yield WireSources(sources=tuple(_sources_of_blocks(blocks)))

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
