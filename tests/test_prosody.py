"""Prosody metrics and the two-stroke judge dimension.

The prosody metrics are report-only. That is a design decision with a test
behind it, not a convenience: gating on a device would let the scorer demand
it, which is pastiche by instruction. So these tests pin three things: the
detectors find what they claim to find, they never move a score, and the six
calibration fixtures still land exactly where the README says they do.
"""

import pytest

import gonzo.scoring.metrics as metrics
from gonzo.scoring.judge import JudgeVerdict
from gonzo.scoring.metrics import (
    _capitalized_abstractions,
    _cliff_drops,
    _polysyndeton_runs,
    _still_points,
    _syllables,
    score_text,
)

LONG = " ".join(["word"] * 40) + "."


class TestDetectors:
    def test_cliff_drop_is_long_then_very_short(self):
        assert _cliff_drops([40, 2]) == 1
        assert _cliff_drops([40, 5]) == 0     # five words is not a cliff
        assert _cliff_drops([20, 2]) == 0     # nothing long to fall from
        assert _cliff_drops([2, 40]) == 0     # the inverse is a still point

    def test_still_point_precedes_a_long_sentence_within_the_window(self):
        assert _still_points([5, 10, 13, 45]) == 1   # the wave-speech shape
        assert _still_points([5, 10, 13, 12, 45]) == 0  # outside the window
        assert _still_points([7, 45]) == 0           # too long to be still

    def test_polysyndeton_needs_three_ands(self):
        assert _polysyndeton_runs(["and this and that and the other"]) == 1
        assert _polysyndeton_runs(["this and that"]) == 0
        assert _polysyndeton_runs(["Andrew and Sandra"]) == 0  # word boundaries

    def test_capitalized_abstractions_skip_the_first_word_and_proper_nouns(self):
        assert _capitalized_abstractions(["He feared the Law and the Enemy."]) == 2
        assert _capitalized_abstractions(["Law school was a mistake."]) == 0
        assert _capitalized_abstractions(["He drove to Denver."]) == 0
        assert _capitalized_abstractions(["He read the LAW aloud."]) == 0  # shouting

    def test_syllables_tell_one_from_many(self):
        for w in ("broke", "rolled", "back", "place", "wave", "the"):
            assert _syllables(w) == 1, w
        for w in ("finally", "inevitable", "victory"):
            assert _syllables(w) > 1, w

    def test_spaced_ellipses_are_counted_separately_from_plain_ones(self):
        report = score_text("He waited. . . nothing came... the line went dead. . .")
        assert report.spaced_ellipses == 2

    def test_report_carries_the_fields(self):
        report = score_text(LONG + " Gone.")
        assert report.cliff_drops == 1
        assert "cliff-drops 1" in report.summary()
        assert "reported, not gated" in report.summary()
        assert "cliff_drops" in report.to_dict()


class TestNeverGated:
    @pytest.mark.parametrize(
        "fixture,expected",
        [
            ("flat_text", 39.2),
            ("pastiche_text", 34.8),
            ("no_elegiac_text", 83.0),
            ("target_text", 100.0),
            ("target_manic_text", 100.0),
            ("target_domestic_text", 100.0),
        ],
    )
    def test_calibration_scores_are_unchanged(self, request, fixture, expected):
        """The exact scores the README publishes, held through this pass."""
        assert score_text(request.getfixturevalue(fixture)).score == expected

    def test_prosody_values_cannot_move_a_score(self, monkeypatch, target_text, flat_text):
        """Mutation-style guard: blow every prosody detector up and confirm
        the score and the failures do not change. If someone later wires a
        prosody field into _evaluate or _composite, this fails."""
        before = {t: (score_text(t).score, score_text(t).failures) for t in (target_text, flat_text)}
        monkeypatch.setattr(metrics, "_cliff_drops", lambda _l: 999)
        monkeypatch.setattr(metrics, "_still_points", lambda _l: 999)
        monkeypatch.setattr(metrics, "_polysyndeton_runs", lambda _s: 999)
        monkeypatch.setattr(metrics, "_capitalized_abstractions", lambda _s: 999)
        monkeypatch.setattr(metrics, "_elegiac_monosyllable_share", lambda _s, _b: 1.0)
        for t, (score, failures) in before.items():
            report = score_text(t)
            assert report.cliff_drops == 999
            assert report.score == score
            assert report.failures == failures


def _verdict(**overrides):
    base = dict(
        aim=10, anchoring=10, register_control=10, elegiac_quality=10,
        argument=10, originality=10, scene_craft=10, velocity=10, comedy=10,
        two_stroke=10, reads_as_pastiche=False,
        strongest="s", weakest="w", fixes=[],
    )
    base.update(overrides)
    return JudgeVerdict(**base)


class TestTwoStrokeJudge:
    def test_perfect_piece_still_scores_100(self):
        """The new weight came out of two existing ones, so the scale holds."""
        assert _verdict().score == 100.0

    def test_two_stroke_carries_weight(self):
        assert _verdict(two_stroke=0).score == pytest.approx(95.0)

    def test_two_stroke_is_required_in_the_schema(self):
        assert "two_stroke" in JudgeVerdict.model_json_schema()["required"]

    def test_two_stroke_clamps_like_the_rest(self):
        assert _verdict(two_stroke=14).two_stroke == 10
        assert _verdict(two_stroke=-2).two_stroke == 0

    def test_rubric_names_the_announced_drop(self):
        from gonzo.scoring.judge import JUDGE_SYSTEM

        assert "TWO STROKES" in JUDGE_SYSTEM
        assert "announces itself" in JUDGE_SYSTEM
