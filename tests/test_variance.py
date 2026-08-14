"""Variance Director.

`temperature` is rejected on Opus 5, so variance is engineered rather than
sampled. These tests pin the two properties that buys us: reproducibility and
genuine spread.
"""

import collections

from gonzo.style.spec import load_spec
from gonzo.style.variance import VarianceDirector


class TestDeterminism:
    def test_same_seed_reproduces(self):
        d = VarianceDirector()
        assert d.draw(seed=7).to_dict() == d.draw(seed=7).to_dict()

    def test_same_seed_reproduces_longform(self):
        d = VarianceDirector()
        assert d.draw(seed=7, longform=True).to_dict() == d.draw(seed=7, longform=True).to_dict()

    def test_different_seeds_diverge(self):
        d = VarianceDirector()
        assert d.draw(seed=1).to_dict() != d.draw(seed=2).to_dict()

    def test_basis_derives_a_stable_seed(self):
        """Same assignment reproduces; different assignments diverge."""
        d = VarianceDirector()
        assert d.draw(basis="the zoning board").seed == d.draw(basis="the zoning board").seed
        assert d.draw(basis="the zoning board").seed != d.draw(basis="the airport").seed


class TestSpread:
    """Variance is the whole point — a director that always picks the same
    thing is worse than the temperature knob it replaces."""

    def test_opening_moves_spread(self):
        d = VarianceDirector()
        seen = collections.Counter(d.draw(seed=i).opening_move for i in range(120))
        assert len(seen) >= 5, f"opening moves collapsed: {seen}"

    def test_imagery_domains_spread(self):
        d = VarianceDirector()
        seen = collections.Counter(d.draw(seed=i).imagery_domain for i in range(120))
        assert len(seen) >= 6, f"imagery domains collapsed: {seen}"

    def test_templates_spread(self):
        d = VarianceDirector()
        seen = {d.draw(seed=i, longform=True).template for i in range(120)}
        assert len(seen) == len(load_spec().templates)


class TestBandRules:
    def test_elegiac_is_never_dominant(self):
        """It earns its force by arriving as contrast, never as the baseline."""
        d = VarianceDirector()
        assert all(d.draw(seed=i).dominant_band != "elegiac" for i in range(200))

    def test_longform_always_mandates_elegiac(self):
        """A filed piece without the break reads as pastiche — so it is never
        left to chance."""
        d = VarianceDirector()
        assert all(d.draw(seed=i, longform=True).contrast_band == "elegiac" for i in range(200))

    def test_contrast_always_differs_from_dominant(self):
        d = VarianceDirector()
        assert all(
            (lambda x: x.contrast_band != x.dominant_band)(d.draw(seed=i)) for i in range(200)
        )


class TestRendering:
    def test_renders_required_fields(self):
        text = VarianceDirector().draw(seed=3, longform=True).render()
        for field in ("OPENING MOVE", "DOMINANT REGISTER", "REQUIRED CONTRAST REGISTER",
                      "IMAGERY DOMAIN", "STRUCTURE"):
            assert field in text

    def test_shortform_omits_structure(self):
        assert "STRUCTURE" not in VarianceDirector().draw(seed=3, longform=False).render()

    def test_instructs_against_leaking_itself(self):
        """The directive is scaffolding; it must not show up as headings."""
        assert "Never mention it" in VarianceDirector().draw(seed=3).render()
