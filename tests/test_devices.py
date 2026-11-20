"""The prosody pass on the style system: spec sections, device focus, stances.

Three properties matter most here. The new draws must not change what any
existing seed reproduces. A restyle must never be handed a device that adds
content. And the Correspondent's guardrails must come through untouched.
"""

import collections
import hashlib
import json
from pathlib import Path

from gonzo.persona import GUARDRAILS
from gonzo.style.spec import load_spec
from gonzo.style.variance import VarianceDirector

SNAPSHOT = Path(__file__).parent / "fixtures" / "variance_snapshot_pre_devices.json"


def _norm(text: str) -> str:
    return " ".join(text.split())


class TestSpecSections:
    def test_two_stroke_engine_reaches_the_prompt(self):
        prompt = _norm(load_spec().system_prompt())
        assert "The Two-Stroke Engine" in prompt
        for move in ("The cliff-drop.", "The still point.", "The elegiac cadence.",
                     "Written for the ear, at second gear."):
            assert move in prompt

    def test_sentence_devices_reach_the_prompt_with_a_budget(self):
        prompt = _norm(load_spec().system_prompt())
        assert "The Sentence Devices" in prompt
        assert "A piece that fires all eight is a demonstration, not a piece" in prompt

    def test_statutory_splice_forbids_quoting_from_memory(self):
        """A real statute quoted wrong is an invented fact about a real
        institution — the exact thing the guardrails forbid."""
        prompt = _norm(load_spec().system_prompt())
        assert "Quote a real document only when you are certain it exists" in prompt
        assert "A real statute, policy, or document is quoted from memory as if verbatim" in prompt

    def test_spec_carries_no_thompson_text(self):
        """The spec describes mechanics; it must not smuggle in his lines."""
        text = load_spec().text.lower()
        for line in ("where the wave finally broke", "our energy would simply prevail",
                     "buy the ticket", "the edge..."):
            assert line not in text

    def test_correspondent_guardrails_are_unchanged(self):
        """The prosody pass and the Morgue may not loosen the Correspondent's
        constraints by a single byte."""
        digest = hashlib.sha256(GUARDRAILS.encode()).hexdigest()
        assert digest == "667542164319d054782749b056848d999c7b274c0eb17fd68528a31df5a3f0fc"

    def test_devices_and_stances_load(self):
        spec = load_spec()
        assert len(spec.devices) == 8
        assert {s["name"] for s in spec.stances} == {
            "clinician", "craft_formalist", "courtly_ironist", "jeremiad",
        }


class TestBackwardCompatibility:
    def test_every_pre_existing_field_reproduces(self):
        """Snapshot taken before the device and stance axes existed. The new
        axes draw last, so every old field of every seed must match exactly."""
        snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        director = VarianceDirector()
        for key, old in snapshot.items():
            seed, longform, structure = key.split("|")
            new = director.draw(
                seed=int(seed), longform=longform == "True", structure=structure == "True"
            ).to_dict()
            for field, value in old.items():
                got = list(new[field]) if isinstance(new[field], tuple) else new[field]
                assert got == value, f"{key}: {field} changed"


class TestDeviceFocus:
    def test_draws_a_pair_and_spreads(self):
        director = VarianceDirector()
        draws = [director.draw(seed=i) for i in range(200)]
        assert all(len(d.devices) == 2 and len(set(d.devices)) == 2 for d in draws)
        seen = collections.Counter(name for d in draws for name in d.devices)
        assert len(seen) == 8, seen

    def test_restyle_never_draws_a_device_that_adds_facts(self):
        adds = {d["name"] for d in load_spec().devices if d.get("adds_facts")}
        assert adds == {"exact_count_catalog", "statutory_splice"}
        director = VarianceDirector()
        for i in range(300):
            d = director.draw(seed=i, longform=True, structure=False)
            assert not adds & set(d.devices), f"seed {i}: {d.devices}"

    def test_compose_may_draw_them(self):
        director = VarianceDirector()
        seen = {n for i in range(300) for n in director.draw(seed=i, longform=True).devices}
        assert {"exact_count_catalog", "statutory_splice"} <= seen

    def test_render_carries_the_focus(self):
        text = VarianceDirector().draw(seed=3).render()
        assert "DEVICE FOCUS" in text
        assert "leave the other sentence devices quiet" in text

    def test_reproducible(self):
        d = VarianceDirector()
        assert d.draw(seed=41).devices == d.draw(seed=41).devices


class TestStances:
    def test_chat_draws_a_stance_and_spreads(self):
        director = VarianceDirector()
        seen = collections.Counter(director.draw(seed=i).stance for i in range(200))
        assert None not in seen
        assert len(seen) == 4, seen

    def test_long_form_and_restyle_draw_none(self):
        director = VarianceDirector()
        for i in range(100):
            assert director.draw(seed=i, longform=True).stance is None
            assert director.draw(seed=i, longform=True, structure=False).stance is None

    def test_render_carries_the_stance_and_lets_it_yield(self):
        text = VarianceDirector().draw(seed=3).render()
        assert "CONVERSATIONAL STANCE" in text
        assert "along with the device focus and the stance" in _norm(text)

    def test_no_stance_depends_on_fame(self):
        """The persona has no famous name and no public myth to be trapped in."""
        briefs = " ".join(s["brief"] for s in load_spec().stances).lower()
        assert "myth" not in briefs and "famous" not in briefs
