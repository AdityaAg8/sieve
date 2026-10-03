"""The artefact family a training profile writes to (training plan C1).

`models/<family>-s<seed>` is the directory every downstream reader names (`bench/ablations.yaml`,
`sieve.model.select`). Speed variants of one recipe (`deberta`, `deberta-nockpt`) share a family,
so whichever one §13 step 7 picks lands where the ablations look. Only YAML is imported here: the
Makefile calls this to build MODEL_DIR, and it should not pay for torch to print one word.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import yaml

CONFIG = Path("bench/train.yaml")


def family(config_path: Path, profile: str) -> str:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles") or {}
    if profile not in profiles:
        raise KeyError(f"no profile {profile!r} in {config_path}; have {sorted(profiles)}")
    name = profiles[profile].get("family")
    if not name:
        raise KeyError(f"profile {profile!r} in {config_path} declares no family")
    return str(name)


def main(argv: Sequence[str] | None = None) -> str:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.family")
    parser.add_argument("profile")
    parser.add_argument("--config", type=Path, default=CONFIG)
    ns = parser.parse_args(argv)
    name = family(ns.config, ns.profile)
    print(name)
    return name


if __name__ == "__main__":  # pragma: no cover
    main()
