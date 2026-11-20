"""Research mode: the Morgue.

Answers questions about Hunter S. Thompson from the record, in plain
scholarly prose. Unlike chat, it draws no variance directive: a research desk
wants the same careful register every turn, and the only per-turn content is
the user's question. That keeps every message clean and the whole system
prefix cacheable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from gonzo.client import GonzoClient, StreamReset, WireSearch, WireSource, WireSources
from gonzo.config import wire_tool
from gonzo.research.morgue import morgue_system_prompt


@dataclass
class MorgueSession:
    """A running research conversation.

    `wire` attaches the web search tool and swaps the Morgue's capability
    section to match, for the same reason as in ChatSession: the narrator's
    claims about its machinery must be true in both directions.
    """

    client: GonzoClient
    wire: bool = False
    history: list[dict[str, Any]] = field(default_factory=list)
    last_sources: list[WireSource] = field(default_factory=list)

    def _messages(self, user_text: str) -> list[dict[str, Any]]:
        return [*self.history, {"role": "user", "content": user_text}]

    def _request(self, user_text: str) -> dict[str, Any]:
        return {
            "system_prompt": morgue_system_prompt(wire=self.wire),
            "messages": self._messages(user_text),
            "mode": "morgue",
            "tools": [wire_tool()] if self.wire else None,
        }

    def send(self, user_text: str) -> str:
        completion = self.client.complete(**self._request(user_text))
        self.last_sources = list(getattr(completion, "sources", []) or [])
        self._record(user_text, completion.text)
        return completion.text

    def stream(self, user_text: str) -> Iterator[str | StreamReset | WireSearch | WireSources]:
        """Yield text deltas; a StreamReset clears what the abandoned model
        produced, so history keeps only the reply that stands."""
        chunks: list[str] = []
        self.last_sources = []
        for chunk in self.client.stream(**self._request(user_text)):
            if isinstance(chunk, StreamReset):
                chunks.clear()
            elif isinstance(chunk, WireSources):
                self.last_sources = list(chunk.sources)
            elif isinstance(chunk, str):
                chunks.append(chunk)
            yield chunk
        self._record(user_text, "".join(chunks))

    def _record(self, user_text: str, reply: str) -> None:
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})

    def reset(self) -> None:
        self.history.clear()
