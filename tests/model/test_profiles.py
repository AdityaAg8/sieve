"""The training profiles of plan §6.4 (training plan B10)."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest

from sieve.model.train import load_profile, main, training_arguments

CONFIG = Path("bench/train.yaml")

# profile: (family, mode, num_labels, max_length, variant)
EXPECTED = {
    "deberta": ("deberta-v3-base", "full", 3, 512, "bs16-ckpt"),
    "deberta-nockpt": ("deberta-v3-base", "full", 3, 512, "bs8x2"),
    "modernbert": ("modernbert-base", "full", 3, 512, "bs16"),
    "modernbert-acc": ("modernbert-base", "full", 3, 512, "bs8x2"),
    "deberta-desc": ("deberta-v3-base-desc", "description_only", 3, 512, "bs16-ckpt"),
    "deberta-scanner": ("deberta-v3-base-scanner", "full", 3, 512, "bs16-ckpt"),
    "deberta-2c": ("deberta-v3-base-2c", "full", 2, 512, "bs16-ckpt"),
    "modernbert-1024": ("modernbert-base-1024", "full", 3, 1024, "bs8x2"),
}


@pytest.mark.parametrize("profile", sorted(EXPECTED))
def test_each_profile_names_its_family_mode_head_length_and_variant(profile: str) -> None:
    config = load_profile(CONFIG, profile, fold=0, seed=0)
    family, mode, num_labels, max_length, variant = EXPECTED[profile]
    assert (config.family, config.mode, config.num_labels) == (family, mode, num_labels)
    assert (config.max_length, config.variant) == (max_length, variant)
    assert config.revision is not None


@pytest.mark.parametrize("profile", [*sorted(EXPECTED), "tiny"])
def test_the_effective_batch_is_16_for_every_profile(profile: str, tmp_path: Path) -> None:
    config = load_profile(CONFIG, profile, fold=0, seed=0)
    assert config.effective_batch_size == 16
    args = training_arguments(config, tmp_path, has_early=True)
    assert args.per_device_train_batch_size * args.gradient_accumulation_steps == 16
    assert args.per_device_eval_batch_size == 2 * config.batch_size


def test_variants_change_only_speed_and_memory() -> None:
    """A variant shares everything that changes the maths with its headline profile (§6.4)."""
    maths = ("base_model", "revision", "max_length", "lr", "warmup_ratio", "epochs", "patience",
             "precision", "class_weights", "mode", "num_labels", "family")  # fmt: skip
    for headline, variant in (("deberta", "deberta-nockpt"), ("modernbert", "modernbert-acc")):
        a = load_profile(CONFIG, headline, fold=0, seed=0).model_dump(include=set(maths))
        b = load_profile(CONFIG, variant, fold=0, seed=0).model_dump(include=set(maths))
        assert a == b


def test_deberta_profiles_train_in_fp32() -> None:
    for profile in EXPECTED:
        config = load_profile(CONFIG, profile, fold=0, seed=0)
        if "deberta" in config.base_model:
            assert config.precision == "fp32"


def test_the_scanner_profile_carries_its_findings() -> None:
    config = load_profile(CONFIG, "deberta-scanner", fold=0, seed=0)
    assert config.scanner_features
    assert config.findings == Path("bench/results/baseline_raw.json")
    for profile in set(EXPECTED) - {"deberta-scanner"}:
        assert not load_profile(CONFIG, profile, fold=0, seed=0).scanner_features


@pytest.mark.parametrize(
    ("profile", "override"),
    [("deberta-2c", {"num_labels": 3}), ("deberta", {"num_labels": 2}),
     ("deberta-desc", {"mode": "full"}), ("deberta", {"mode": "description_only"})],
)  # fmt: skip
def test_a_flag_that_contradicts_the_profile_is_refused(
    profile: str, override: dict[str, Any]
) -> None:
    with pytest.raises(ValueError, match="profile"):
        load_profile(CONFIG, profile, fold=0, seed=0, **override)


def test_a_flag_that_agrees_with_the_profile_is_accepted() -> None:
    config = load_profile(CONFIG, "deberta-2c", fold=0, seed=0, num_labels=2, mode="full")
    assert config.num_labels == 2


def test_the_smoke_profile_still_takes_mode_and_head_from_the_flags() -> None:
    assert load_profile(CONFIG, "tiny", fold=0, seed=0, num_labels=2).num_labels == 2
    config = load_profile(CONFIG, "tiny", fold=0, seed=0, mode="description_only")
    assert config.mode == "description_only"
    assert load_profile(CONFIG, "tiny", fold=0, seed=0).mode == "full"


@pytest.mark.parametrize("profile", sorted(EXPECTED))
def test_the_published_training_command_reloads_the_same_config(
    profile: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_profile(CONFIG, profile, fold=2, seed=1)
    command = config.training_command(Path("bench/data/labelled.jsonl"), Path("models/x"))
    seen: dict[str, Any] = {}
    monkeypatch.setattr("sieve.model.train.run", lambda c, corpus, out: seen.setdefault("c", c))
    main(shlex.split(command)[3:])  # drop "python -m sieve.model.train"
    assert seen["c"] == config
