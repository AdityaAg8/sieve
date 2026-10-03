"""Artefact family names (training plan C1, finding F4).

Every speed variant of one recipe writes the same `models/<family>-s<seed>`, and that is the
name `bench/ablations.yaml` points at. The Makefile used to name directories after the profile
(`models/deberta-s0`), which no ablation row ever read.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from bench.ablations.run import RowSpec, seed_dir
from sieve.model.family import family

CONFIG = Path("bench/train.yaml")

EXPECTED = {
    "deberta": "deberta-v3-base",
    "deberta-nockpt": "deberta-v3-base",
    "modernbert": "modernbert-base",
    "modernbert-acc": "modernbert-base",
    "deberta-desc": "deberta-v3-base-desc",
    "deberta-scanner": "deberta-v3-base-scanner",
    "deberta-2c": "deberta-v3-base-2c",
    "modernbert-1024": "modernbert-base-1024",
    "tiny": "tiny",
}


def _profiles() -> list[str]:
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return sorted(raw["profiles"])


def test_every_profile_names_its_family() -> None:
    assert {p: family(CONFIG, p) for p in _profiles()} == EXPECTED


def test_an_unknown_profile_is_refused() -> None:
    with pytest.raises(KeyError, match="no profile"):
        family(CONFIG, "debarta")


def test_the_cli_prints_the_family(capsys: pytest.CaptureFixture[str]) -> None:
    from sieve.model.family import main

    main(["deberta-nockpt"])
    assert capsys.readouterr().out == "deberta-v3-base\n"


def _make_model_dir(profile: str, seed: int) -> str:
    make = shutil.which("make")
    if make is None:
        pytest.skip("make is not installed")
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell; profiles come from train.yaml
        [make, "-s", "--no-print-directory", "model-dir", f"PROFILE={profile}", f"SEED={seed}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return out.stdout.strip()


def test_every_ablation_row_resolves_against_the_makefiles_tree(tmp_path: Path) -> None:
    seeds = (0, 1, 2)
    made: dict[Path, str] = {}
    for profile in _profiles():
        for seed in seeds:
            directory = Path(_make_model_dir(profile, seed))
            assert directory == Path("models") / f"{EXPECTED[profile]}-s{seed}"
            (tmp_path / directory).mkdir(parents=True, exist_ok=True)
            made.setdefault(directory, EXPECTED[profile])

    rows = yaml.safe_load(Path("bench/ablations.yaml").read_text(encoding="utf-8"))["rows"]
    seeded = [RowSpec(**row) for row in rows if RowSpec(**row).seeded]
    assert seeded
    for spec in seeded:
        for seed in seeds:
            resolved = seed_dir(spec.classifier, seed)
            assert (tmp_path / resolved).is_dir(), (spec.row, resolved)
            # Seed substitution, not a glob: `deberta-v3-base-s*` as a glob would also match the
            # scanner family. The resolved directory is exactly one family's.
            assert resolved.name == f"{made[resolved]}-s{seed}"


def test_variants_of_one_recipe_share_a_directory() -> None:
    assert _make_model_dir("deberta", 1) == _make_model_dir("deberta-nockpt", 1)
    assert _make_model_dir("modernbert", 0) == _make_model_dir("modernbert-acc", 0)


def test_calibrate_names_the_seed_directory_after_the_family(
    dev_records: list[Any], tmp_path: Path
) -> None:
    """A variant's folds (model_version `deberta-nockpt@...`) pool into the family's directory."""
    from sieve.model.calibrate import main
    from sieve.model.data import LABEL2ID

    rng = np.random.default_rng(0)
    corpus = tmp_path / "c.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in dev_records), encoding="utf-8", newline="\n"
    )
    dirs = []
    for fold in range(5):
        held = [r for r in dev_records if r.fold == fold]
        d = tmp_path / "models" / f"deberta-v3-base-f{fold}-s0"
        d.mkdir(parents=True)
        labels = np.asarray([LABEL2ID[r.label] for r in held], dtype=np.int64)
        logits = rng.normal(size=(len(held), 3))
        logits[np.arange(len(held)), labels] += 2.5
        np.save(d / "oof_logits.npy", logits.astype(np.float32))
        np.save(d / "oof_labels.npy", labels)
        (d / "oof_ids.json").write_text(json.dumps([r.id for r in held]), encoding="utf-8")
        config = {"model_version": "deberta-nockpt@abc1234", "family": "deberta-v3-base",
                  "fold": fold, "seed": 0, "num_labels": 3, "t_low": None}  # fmt: skip
        (d / "sieve_config.json").write_text(json.dumps(config), encoding="utf-8")
        dirs.append(d)
    written = main(["--dirs", *map(str, dirs), "--corpus", str(corpus), "--results-dir",
                    str(tmp_path / "r"), "--n-boot", "20", "--curve-boot", "10",
                    "--curve-points", "5"])  # fmt: skip
    assert written == tmp_path / "models" / "deberta-v3-base-s0" / "sieve_config.json"
