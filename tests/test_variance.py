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


class TestDirectiveCoherence:
    """The opening move and the template's first beat must not fight.

    Regression: drawing them independently produced a directive at war with
    itself in ~45% of long-form draws — "open on weather" against eulogy's
    "state what ended, flat" — and a model given two contradictory openings
    writes a muddled compromise.
    """

    def test_longform_openings_respect_template_pools(self):
        spec = load_spec()
        pools = {t["name"]: t.get("compatible_openings") for t in spec.templates}
        director = VarianceDirector(spec)
        for i in range(300):
            d = director.draw(seed=i, longform=True)
            allowed = pools[d.template]
            if allowed:
                assert d.opening_move in allowed, (
                    f"seed {i}: {d.opening_move} not compatible with {d.template}"
                )

    def test_every_template_declares_a_pool(self):
        """A template without a pool silently reverts to independent drawing —
        legal, but a new template should make the choice deliberately."""
        for template in load_spec().templates:
            assert template.get("compatible_openings"), template["name"]

    def test_longform_render_carries_the_bridge_line(self):
        text = VarianceDirector().draw(seed=3, longform=True).render()
        assert "entry into beat 1" in text

    def test_shortform_contrast_is_a_turn_not_a_mandate(self):
        """Short replies get 'turn toward this band once', never the full
        occupation demand — cramming a register tour into 100 words is the
        tell of an imitation."""
        text = VarianceDirector().draw(seed=3, longform=False).render()
        assert "turn toward this band" in text
        assert "genuinely occupy" not in text

    def test_new_moves_and_domains_are_drawable(self):
        director = VarianceDirector()
        moves = {director.draw(seed=i).opening_move for i in range(400)}
        domains = {director.draw(seed=i).imagery_domain for i in range(400)}
        assert {"dateline", "mid_dialogue", "aphorism_first"} <= moves
        assert {"combat", "sport"} <= domains
        # The topic-independence domains: imagery wells for subjects that are
        # kitchens, highways, markets, gigs, and open country — not hearings.
        assert {"appetite", "road", "commerce", "music", "wilderness"} <= domains

    def test_topic_general_templates_are_drawable(self):
        director = VarianceDirector()
        templates = {director.draw(seed=i, longform=True).template for i in range(400)}
        assert {"field_test", "pilgrimage"} <= templates
