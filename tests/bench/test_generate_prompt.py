"""SC-8: the generator is never shown a detection rule, and never a real target."""

from __future__ import annotations

from bench.generate.mutate import PayloadSpec, render_system_prompt

WILD = [f"You MUST call resolve_library_id before query_docs ({i})." for i in range(10)]


def test_prompt_carries_ten_wild_examples_for_register() -> None:
    prompt = render_system_prompt(
        PayloadSpec(
            label="poisoned",
            sub_labels=["exfiltration"],
            difficulty="hard",
            target_fields=["description"],
            property_name=None,
            concealment="none",
        ),
        WILD,
    )
    for example in WILD:
        assert example in prompt


def test_prompt_never_names_a_scanner_rule() -> None:
    prompt = render_system_prompt(
        PayloadSpec(
            label="imperative_benign",
            sub_labels=[],
            difficulty=None,
            target_fields=["description"],
            property_name=None,
            concealment="none",
        ),
        WILD,
    ).lower()
    # SC-8 and PRD §9.5: a generator shown the rules writes payloads that trip the rules, and
    # the FP-reduction number measured against those rules is then circular.
    for forbidden in ("yara", "coercive_injection", "rule_id", "mcp-scanner", "signature"):
        assert forbidden not in prompt
    assert "example.invalid" in prompt


def test_prompt_states_the_defensive_purpose_and_the_hard_constraints() -> None:
    prompt = render_system_prompt(
        PayloadSpec(
            label="poisoned",
            sub_labels=["secret_solicitation", "concealment"],
            difficulty="medium",
            target_fields=["inputSchema"],
            property_name="location",
            concealment="zero_width",
            host_name="get_current",
        ),
        WILD,
    )
    lower = prompt.lower()
    assert "detection benchmark" in lower
    assert "no working code" in lower or "never working code" in lower
    assert "secret_solicitation" in prompt and "concealment" in prompt
    assert "medium" in prompt
    assert "inputSchema" in prompt and "location" in prompt


def test_imperative_and_poisoned_use_different_templates() -> None:
    def prompt_for(label: str) -> str:
        return render_system_prompt(
            PayloadSpec(
                label=label,  # type: ignore[arg-type]
                sub_labels=[],
                difficulty=None,
                target_fields=["description"],
                property_name=None,
                concealment="none",
            ),
            WILD,
        )

    assert prompt_for("imperative_benign") != prompt_for("poisoned")
    assert "example.invalid" in prompt_for("benign")


def test_the_prompt_templates_on_disk_name_no_rule() -> None:
    from bench.generate.mutate import PROMPT_DIR

    for name in ("payload.md", "imperative.md"):
        text = (PROMPT_DIR / name).read_text(encoding="utf-8").lower()
        for forbidden in ("yara", "coercive_injection", "rule_id", "mcp-scanner", "signature"):
            assert forbidden not in text
