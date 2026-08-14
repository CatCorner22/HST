"""Conversational mode.

Holds history, draws a fresh variance directive per turn so successive replies
don't converge on one register, and streams.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from gonzo.client import GonzoClient, StreamReset
from gonzo.config import MODES
from gonzo.style.spec import StyleSpec, load_spec
from gonzo.style.variance import VarianceDirective, VarianceDirector


@dataclass
class ChatSession:
    """A running conversation."""

    client: GonzoClient
    spec: StyleSpec = field(default_factory=load_spec)
    seed: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    last_directive: VarianceDirective | None = None

    def __post_init__(self) -> None:
        self._director = VarianceDirector(self.spec)
        self._turn = 0

    def _messages(self, user_text: str) -> list[dict[str, Any]]:
        """History, then this turn's directive, then the user's line.

        The directive goes in `messages` rather than `system` on purpose: the
        system prefix has to stay byte-identical for the prompt cache to hold,
        and this block changes every turn.
        """
        # Vary the seed per turn so a fixed session seed still produces motion
        # across the conversation while staying reproducible end to end.
        turn_seed = None if self.seed is None else self.seed + self._turn
        directive = self._director.draw(seed=turn_seed, basis=user_text, longform=False)
        self.last_directive = directive

        cfg = MODES["chat"]
        return [
            *self.history,
            {
                "role": "user",
                "content": (
                    f"{directive.render()}\n\n"
                    f"<length>{cfg.length_note}</length>\n\n"
                    f"{user_text}"
                ),
            },
        ]

    def send(self, user_text: str) -> str:
        """Send a turn, return the full reply."""
        completion = self.client.complete(
            system_prompt=self.spec.system_prompt(),
            messages=self._messages(user_text),
            mode="chat",
        )
        self._record(user_text, completion.text)
        return completion.text

    def stream(self, user_text: str) -> Iterator[str | StreamReset]:
        """Send a turn, yielding text deltas.

        A `StreamReset` is passed through to the caller and also clears the
        locally accumulated text, so the history records only the reply that
        actually stands -- not the abandoned output of a model that declined
        partway through.
        """
        chunks: list[str] = []
        for chunk in self.client.stream(
            system_prompt=self.spec.system_prompt(),
            messages=self._messages(user_text),
            mode="chat",
        ):
            if isinstance(chunk, StreamReset):
                chunks.clear()
            else:
                chunks.append(chunk)
            yield chunk
        self._record(user_text, "".join(chunks))

    def _record(self, user_text: str, reply: str) -> None:
        """Store the *clean* user turn in history.

        The directive and length note are scaffolding for one request; keeping
        them in history would bloat the context and let stale assignments bleed
        into later turns.
        """
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": reply})
        self._turn += 1

    def reset(self) -> None:
        self.history.clear()
        self._turn = 0
