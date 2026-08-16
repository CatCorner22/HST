"""Scorer calibration.

A scorer that always says yes is worthless, so these tests pin *both*
directions: target prose must pass, and flat prose and pastiche must fail, for
the specific reasons the style spec says matter.
"""

from gonzo.config import THRESHOLDS
from gonzo.scoring.metrics import _adjective_stacks, score_text


class TestDiscrimination:
    def test_target_passes(self, target_text):
        report = score_text(target_text)
        assert report.passed, f"target prose should pass; failures: {report.failures}"
        assert report.score >= 80

    def test_flat_prose_fails(self, flat_text):
        report = score_text(flat_text)
        assert not report.passed
        assert report.score < 50

    def test_pastiche_fails(self, pastiche_text):
        report = score_text(pastiche_text)
        assert not report.passed
        assert report.score < 60

    def test_ordering_is_strict(self, flat_text, pastiche_text, target_text):
        """Target must outscore both negative controls by a clear margin."""
        flat = score_text(flat_text).score
        pastiche = score_text(pastiche_text).score
        target = score_text(target_text).score
        assert target > flat + 25
        assert target > pastiche + 25


class TestFailureReasons:
    """Failures must name the right defect, not merely fail."""

    def test_flat_prose_flagged_for_rhythm_and_anchoring(self, flat_text):
        failures = " ".join(score_text(flat_text).failures)
        assert "uniform rhythm" in failures
        assert "unanchored" in failures

    def test_pastiche_flagged_for_tic_budget(self, pastiche_text):
        report = score_text(pastiche_text)
        assert report.tic_rate_per_500 > report.tic_budget
        assert any("pastiche" in f for f in report.failures)

    def test_pastiche_flagged_for_unanchored_hyperbole(self, pastiche_text):
        """All volume, no specifics — the defining failure of imitation."""
        report = score_text(pastiche_text)
        assert report.specificity_density < THRESHOLDS["specificity_density"]

    def test_flat_prose_is_not_read_as_elegiac(self, flat_text):
        """Plainness is not mourning.

        Regression: flat administrative prose is also short and unadorned, and
        an earlier version scored it elegiac on structure alone.
        """
        assert not score_text(flat_text).has_elegiac


class TestRegisterDetection:
    def test_target_moves_between_bands(self, target_text):
        report = score_text(target_text)
        assert report.register_transitions >= THRESHOLDS["min_register_transitions"]
        assert report.has_elegiac

    def test_longform_without_elegiac_fails(self, no_elegiac_text):
        """Competent long-form with no elegiac break must still fail.

        This is the engine's central thesis under test: believability comes
        from register dynamics, not intensity. The fixture is good prose --
        varied rhythm, dense specifics, real targets -- and it fails anyway.
        """
        report = score_text(no_elegiac_text)
        assert report.is_longform
        assert not report.has_elegiac
        assert not report.passed
        assert any("elegiac" in f for f in report.failures)

    def test_no_elegiac_fixture_is_otherwise_strong(self, no_elegiac_text):
        """Confirms the failure above is about register, not general quality."""
        report = score_text(no_elegiac_text)
        assert report.score > 70
        assert not any(
            kind in " ".join(report.failures)
            for kind in ("uniform rhythm", "unanchored", "pastiche")
        )


class TestCueMatching:
    """Regression guards for cue matching.

    The original bug: cues were matched with `cue in text`, so "ended" fired on
    "attended" and "lost" on "closest". Asserting that via score_text no longer
    works -- those words were removed from the lexicon during calibration, so
    the test passed for the wrong reason and could not fail. Test the helper
    directly against cues that DO appear as substrings.
    """

    def test_cue_hits_respect_word_boundaries(self):
        from gonzo.scoring.metrics import _cue_hits

        assert _cue_hits("everyone attended the session", ["ended"]) == 0
        assert _cue_hits("the closest estimate", ["lost"]) == 0
        assert _cue_hits("the recovery is over", ["over"]) == 1

    def test_multiword_cues_still_match(self):
        from gonzo.scoring.metrics import _cue_hits

        assert _cue_hits("that shop is no longer there", ["no longer"]) == 1
        assert _cue_hits("nothing lingers longer", ["no longer"]) == 0

    def test_distinct_cue_counting(self):
        from gonzo.scoring.metrics import _distinct_cues

        assert _distinct_cues("no longer, and no longer again", ["no longer"]) == 1
        assert _distinct_cues("no longer and used to", ["no longer", "used to"]) == 2


class TestAdjectiveStacks:
    def test_consecutive_run(self):
        assert _adjective_stacks("a vicious bloated atavistic swine") == 1

    def test_comma_coordinate_run(self):
        """Commas signal coordination even when morphology does not."""
        assert _adjective_stacks("a thick, sweet, ammoniac wall") == 1

    def test_two_adjectives_is_not_a_stack(self):
        assert _adjective_stacks("the big red car") == 0

    def test_ordinary_prose_has_none(self):
        assert _adjective_stacks("Attendance was good and departments were represented.") == 0


class TestEdgeCases:
    def test_empty_input(self):
        report = score_text("")
        assert not report.passed
        assert report.word_count == 0

    def test_single_sentence_does_not_crash(self):
        assert score_text("One short line.").word_count == 3


class TestSecondTarget:
    """The dialogue-bearing positive control.

    One positive fixture means calibration is overfit to one register mix.
    This one is loud where target.txt is quiet: savage/manic-dominant, real
    dialogue, an interjection or two, a paranoid-hypothetical digression --
    and it must clear every gate anyway, including the elegiac break.
    """

    def test_passes(self, target_manic_text):
        report = score_text(target_manic_text)
        assert report.passed, f"failures: {report.failures}"
        assert report.score >= 80

    def test_register_mix_is_the_opposite_face(self, target_manic_text):
        report = score_text(target_manic_text)
        loud = report.band_shares.get("savage", 0.0) + report.band_shares.get("manic", 0.0)
        assert loud >= 0.5, f"expected savage+manic dominant, shares: {report.band_shares}"
        assert report.has_elegiac

    def test_scene_craft_is_visible(self, target_manic_text):
        """The metrics the original corpus never exercised must register here."""
        report = score_text(target_manic_text)
        assert report.dialogue_share > 0.04
        assert report.question_rate > 0
        assert report.interjection_rate > 0

    def test_outscores_negative_controls(self, flat_text, pastiche_text, target_manic_text):
        score = score_text(target_manic_text).score
        assert score > score_text(flat_text).score + 25
        assert score > score_text(pastiche_text).score + 25

    def test_tic_budget_respected(self, target_manic_text):
        report = score_text(target_manic_text)
        assert report.tic_rate_per_500 <= report.tic_budget
        assert not report.tic_violations


class TestDomesticTarget:
    """The topic-independence positive control.

    A diner buyout — no senator, no hearing, no election anywhere in it —
    must clear every gate the political fixtures clear. If this fixture
    fails while they pass, the style system has quietly narrowed into a
    politics costume, which is exactly the failure the spec's "The Subject
    Is Never the Limit" section exists to prevent.
    """

    def test_passes(self, target_domestic_text):
        report = score_text(target_domestic_text)
        assert report.passed, f"failures: {report.failures}"
        assert report.score >= 80

    def test_hits_all_four_registers_without_politics(self, target_domestic_text):
        report = score_text(target_domestic_text)
        assert {"savage", "clinical", "manic", "elegiac"} <= set(report.bands), (
            f"expected all four bands on a domestic subject, got {report.bands}"
        )
        assert report.has_elegiac

    def test_needs_no_signature_tics(self, target_domestic_text):
        """The voice without a single borrowed word: zero tics and it still
        reads — and scores — as the style. Tics are a budget, not a fuel."""
        report = score_text(target_domestic_text)
        assert report.tic_count == 0

    def test_outscores_negative_controls(self, flat_text, pastiche_text, target_domestic_text):
        score = score_text(target_domestic_text).score
        assert score > score_text(flat_text).score + 25
        assert score > score_text(pastiche_text).score + 25


class TestSceneCraft:
    """Scene-craft metrics: measured and reported, never gated."""

    def test_dialogue_share_measures_quoted_words(self):
        r = score_text('"Get in the car," he said. The road was empty and long.')
        assert 0.2 < r.dialogue_share < 0.5

    def test_prose_without_dialogue_reports_zero(self, target_text):
        assert score_text(target_text).dialogue_share == 0.0

    def test_question_and_interjection_rates(self):
        text = "Why not? The county recorded the deed on March 4 and nobody said a word."
        r = score_text(text)
        assert r.question_rate == 0.5
        assert r.interjection_rate == 0.5

    def test_second_person_counts_narration_only(self):
        spoken = score_text('"You will regret this," he said. The road was empty tonight.')
        assert spoken.second_person_rate == 0.0
        narrated = score_text("You could smell it from the road, a long way out.")
        assert narrated.second_person_rate > 0

    def test_no_scene_craft_gates(self, target_text):
        """target.txt has zero dialogue and must keep passing untouched."""
        report = score_text(target_text)
        assert report.dialogue_share == 0.0
        assert report.question_rate == 0.0
        assert report.passed

    def test_attribution_is_not_a_phantom_sentence(self):
        from gonzo.scoring.metrics import _sentences

        assert len(_sentences('"You cannot park there!" he said.')) == 1
        assert len(_sentences('"Who signed it?" the clerk asked. He shrugged.')) == 2
        # A quote-final terminator followed by a new sentence still splits.
        assert len(_sentences('"Get out." The room went quiet.')) == 2
