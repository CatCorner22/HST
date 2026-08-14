"""Generation modes: chat, compose, transfer, critique."""

from gonzo.modes.chat import ChatSession
from gonzo.modes.compose import Composer
from gonzo.modes.critique import Critic
from gonzo.modes.transfer import Transferrer

__all__ = ["ChatSession", "Composer", "Critic", "Transferrer"]
