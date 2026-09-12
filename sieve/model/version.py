"""The git SHA that produced an artefact. Every `model_version` string ends with it."""

from __future__ import annotations

import subprocess
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
