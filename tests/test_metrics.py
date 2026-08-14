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
    def test_cues_match_on_word_boundaries(self):
        """Regression: substring matching fired 'ended' on 'attended'."""
        text = "Everyone attended the session. The costs were the closest estimate."
        assert not score_text(text).has_elegiac


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
