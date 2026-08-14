"""Style spec assembly and prompt-cache safety."""

from gonzo.persona import GUARDRAILS, PERSONA
from gonzo.style.spec import load_spec


class TestSpecLoading:
    def test_bands_present(self):
        assert set(load_spec().band_names) == {"savage", "clinical", "manic", "elegiac"}

    def test_templates_have_movements(self):
        for template in load_spec().templates:
            assert template["movements"], f"{template['name']} has no movements"

    def test_every_longform_template_includes_an_elegiac_beat(self):
        """The break is mandatory in long-form, so no template may omit it."""
        for template in load_spec().templates:
            beats = " ".join(template["movements"]).lower()
            assert "elegiac" in beats, f"{template['name']} has no elegiac beat"

    def test_tic_budget_is_tight(self):
        spec = load_spec()
        assert spec.tic_budget_per_500 <= 5
        assert len(spec.tic_words) >= 10


class TestSystemPrompt:
    def test_includes_persona_spec_and_guardrails(self):
        prompt = load_spec().system_prompt()
        assert PERSONA.strip() in prompt
        assert GUARDRAILS.strip() in prompt
        assert "Specificity Anchor" in prompt

    def test_guardrails_come_last(self):
        """Last instruction before the conversation starts, so nothing in the
        style guidance can be read as loosening them.

        Asserts the property directly. The earlier version only checked that
        guardrails followed the persona, which the spec sitting between them
        already guaranteed -- it could not fail no matter how the sections were
        reordered.
        """
        prompt = load_spec().system_prompt()
        assert prompt.rstrip().endswith(GUARDRAILS.strip())

    def test_states_non_impersonation_explicitly(self):
        prompt = load_spec().system_prompt().lower()
        assert "you are not hunter s. thompson" in prompt
        assert "never write under his byline" in prompt

    def test_is_byte_stable_across_calls(self):
        """Prompt-cache safety regression.

        Anything dynamic in the system prefix -- a timestamp, a uuid, a seed --
        silently kills caching for every downstream token. The variance
        directive lives in `messages` precisely to keep this stable.
        """
        assert load_spec().system_prompt() == load_spec().system_prompt()

    def test_contains_no_dynamic_markers(self):
        import re

        prompt = load_spec().system_prompt()
        assert not re.search(r"\b20\d\d-\d\d-\d\d\b", prompt), "date leaked into cached prefix"
        assert not re.search(r"[0-9a-f]{32}", prompt), "uuid/hash leaked into cached prefix"

    def test_long_enough_to_cache(self):
        """Opus 5 will not cache a prefix under 512 tokens; below that the
        breakpoint is a no-op and we would silently pay full price forever."""
        approx_tokens = len(load_spec().system_prompt()) / 4
        assert approx_tokens > 512
