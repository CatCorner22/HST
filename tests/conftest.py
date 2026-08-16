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
def target_manic_text() -> str:
    """Second positive control: savage/manic-dominant, dialogue-bearing.

    target.txt is clinical/elegiac-leaning and contains no dialogue, no
    questions, and no interjections; calibrating against it alone overfits the
    scorer to one register blend. This fixture must pass with the OPPOSITE
    mix — invective and acceleration dominant, real quoted speech, an
    imagined-scenario digression — while still dropping into the mandatory
    elegiac break. The scorer has to accept both faces of the voice.
    """
    return _load("target_manic")


@pytest.fixture(scope="session")
def target_domestic_text() -> str:
    """Third positive control: a domestic subject, no politics anywhere.

    Every other fixture orbits public affairs, which quietly lets the scorer
    and spec require politics for the vibe. The voice's claim is topic
    independence — a diner buyout must clear the same thresholds a hearing
    does, hitting all four registers with zero signature tics. If this
    fixture ever fails while the political targets pass, the style system
    has narrowed into a politics costume.
    """
    return _load("target_domestic")


@pytest.fixture(scope="session")
def no_elegiac_text() -> str:
    """Well-made long-form prose that never drops into the elegiac band.

    The most important negative control in the suite: it scores well on rhythm,
    specificity, and texture, and must still fail. Constant volume with no
    break is the defining tell of imitation.
    """
    return _load("no_elegiac")
