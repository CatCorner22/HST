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

    def test_never_offers_absent_capabilities(self):
        """The narrator must not offer tools the engine does not have.

        Live-output regression: asked about events past its cutoff, the model
        volunteered "turn on web search here and let me pull it" — a feature
        this product does not have. The persona now states the capability
        boundary explicitly; this pins that the statement reaches the model.
        """
        # Normalize hard line wraps so assertions survive prose reflow.
        prompt = " ".join(load_spec().system_prompt().split())
        assert "no search desk" in prompt
        assert "Never offer one" in prompt
        assert "machinery you do not have" in prompt

    def test_long_enough_to_cache(self):
        """Opus 5 will not cache a prefix under 512 tokens; below that the
        breakpoint is a no-op and we would silently pay full price forever."""
        approx_tokens = len(load_spec().system_prompt()) / 4
        assert approx_tokens > 512


class TestWirePrompt:
    """The two persona variants must each tell the truth about the machinery.

    Wireless: the narrator has a file with an end date and nothing else, and
    must never offer a search it cannot run. Wired: the narrator has a real
    search line, must use it for load-bearing facts, and must attribute what
    comes off it. The variants may never blend — a prompt that both denies
    and offers the wire recreates the fabricated-capability bug from PR #4
    in one direction or the other.
    """

    @staticmethod
    def _normalized(wire: bool) -> str:
        return " ".join(load_spec().system_prompt(wire=wire).split())

    def test_default_is_wireless(self):
        assert load_spec().system_prompt() == load_spec().system_prompt(wire=False)

    def test_wire_prompt_installs_the_wire(self):
        wired = self._normalized(wire=True)
        assert "The desk has installed a wire" in wired

    def test_wire_prompt_drops_the_denial(self):
        """The wired narrator must not carry the wireless capability denial —
        a model told both 'you have no search desk' and 'pull the wire' will
        do one of them at random."""
        wired = self._normalized(wire=True)
        assert "no search desk" not in wired
        assert "Never offer one" not in wired

    def test_wireless_prompt_has_no_wire(self):
        plain = self._normalized(wire=False)
        assert "The desk has installed a wire" not in plain

    def test_wire_prompt_demands_attribution(self):
        wired = self._normalized(wire=True)
        assert "What comes off the wire, attribute" in wired
        assert "you invent the imagery, never the facts" in wired

    def test_wire_variant_is_byte_stable(self):
        """Both variants sit in the cache prefix; each must be reproducible."""
        assert load_spec().system_prompt(wire=True) == load_spec().system_prompt(wire=True)

    def test_wire_variant_keeps_guardrails_last(self):
        from gonzo.persona import GUARDRAILS

        prompt = load_spec().system_prompt(wire=True)
        assert prompt.rstrip().endswith(GUARDRAILS.strip())

    def test_wire_variant_long_enough_to_cache(self):
        assert len(load_spec().system_prompt(wire=True)) / 4 > 512
