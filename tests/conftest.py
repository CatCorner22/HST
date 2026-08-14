import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def flat_text() -> str:
    """Flat corporate prose. Must score low — the scorer's negative control."""
    return _load("flat")


@pytest.fixture(scope="session")
def pastiche_text() -> str:
    """All-tic imitation. Must fail the anti-pastiche budget."""
    return _load("pastiche")


@pytest.fixture(scope="session")
def target_text() -> str:
    """Original prose written to spec. Must pass every threshold."""
    return _load("target")


@pytest.fixture(scope="session")
def no_elegiac_text() -> str:
    """Well-made long-form prose that never drops into the elegiac band.

    The most important negative control in the suite: it scores well on rhythm,
    specificity, and texture, and must still fail. Constant volume with no
    break is the defining tell of imitation.
    """
    return _load("no_elegiac")
