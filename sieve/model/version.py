"""The git SHA that produced an artefact. Every `model_version` string ends with it."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from functools import lru_cache

UNKNOWN_SHA = "unknown"


@lru_cache(maxsize=1)
def git_sha(short: bool = True) -> str:
    """The current commit, or `"unknown"` outside a checkout.

    Never raises: an artefact built from a tarball still gets a `model_version`, it just says
    so. The model card, by contrast, refuses to publish without a real SHA (Task 3.9).
    """
    args = ["git", "rev-parse", "--short", "HEAD"] if short else ["git", "rev-parse", "HEAD"]
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            args, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN_SHA
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else UNKNOWN_SHA


#: Paths whose uncommitted changes would make an artefact's SHA a lie. `bench/results` and
#: `bench/data` are excluded: runs write results there, and the corpus is pinned separately by
#: `corpus_sha256` in `metrics.json`.
DIRTY_PATHSPEC = [
    "sieve",
    "bench",
    "pyproject.toml",
    "uv.lock",
    "Makefile",
    ":(exclude)bench/results",
    ":(exclude)bench/data",
]

#: Profiles that never publish anything, so they may run from a dirty tree (CI smoke).
SMOKE_PROFILES = frozenset({"tiny"})


class DirtyTreeRefused(RuntimeError):  # noqa: N818 - a refusal, like PrecisionRefused
    """A real training profile was asked to run from uncommitted code (training plan A5, F26)."""


def dirty_paths() -> list[str]:
    """`git status --porcelain` lines for uncommitted changes under `DIRTY_PATHSPEC`."""
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
            ["git", "status", "--porcelain", "--", *DIRTY_PATHSPEC],  # noqa: S607
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in out.stdout.splitlines() if line.strip()]


def require_clean_tree(
    profile: str, *, allow_dirty: bool, status: Callable[[], list[str]] = dirty_paths
) -> None:
    """Refuse to train a non-smoke profile on uncommitted code, unless told to."""
    if profile in SMOKE_PROFILES or allow_dirty:
        return
    changed = status()
    if changed:
        raise DirtyTreeRefused(
            "uncommitted changes would make this artefact's git SHA a lie; commit them or pass "
            f"--allow-dirty: {changed[:10]}"
        )
