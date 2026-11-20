"""Generation modes: chat, compose, transfer, critique, and the Morgue."""

from gonzo.modes.chat import ChatSession
from gonzo.modes.compose import Composer
from gonzo.modes.critique import Critic
from gonzo.modes.morgue import MorgueSession
from gonzo.modes.transfer import Transferrer

__all__ = ["ChatSession", "Composer", "Critic", "MorgueSession", "Transferrer"]
