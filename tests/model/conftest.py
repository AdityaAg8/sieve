"""Fixtures for the model lane: the real grouped split, and a closed Hugging Face session."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from bench.split.split import DEFAULT_SEED, assign
from sieve.types import CorpusRecord


@pytest.fixture(scope="session", autouse=True)
def _close_the_hub_session() -> Iterator[None]:
    """Close `huggingface_hub`'s pooled TLS connection before the interpreter tears down.

    The hub keeps a cached `requests.Session` per (headers, proxies) key. Left open, its socket
    is finalised by the garbage collector during some later test's teardown, and under the
    project's `filterwarnings = ["error"]` that surfaces as a sporadic
    `ResourceWarning: unclosed <ssl.SSLSocket ...>` ERROR attributed to whichever test happened
    to run last. Closing it here fixes it at the source instead of filtering the symptom.
    """
    yield
    close_hub_session()


def close_hub_session() -> None:
    """Close and drop `huggingface_hub`'s cached session. Never raises; teardown is not a gate."""
    try:
        from huggingface_hub.utils._http import get_session, reset_sessions
    except ImportError:  # pragma: no cover - the hub is always installed with the model extra
        return
    try:
        get_session().close()
        reset_sessions()
    except Exception:  # pragma: no cover  # noqa: BLE001
        return


def _mini_50() -> list[CorpusRecord]:
    lines = Path("fixtures/corpus/mini-50.jsonl").read_text(encoding="utf-8").splitlines()
    return [CorpusRecord.model_validate_json(line) for line in lines]


def assigned_mini_50() -> list[CorpusRecord]:
    """mini-50 through L1's real grouped assignment: `dev` + `holdout`, folds by server."""
    return assign(_mini_50(), seed=DEFAULT_SEED)


@pytest.fixture
def assigned_records() -> list[CorpusRecord]:
    """Every mini-50 row carrying the split L1's `bench.split.split.assign` gave it."""
    return assigned_mini_50()


@pytest.fixture
def dev_records() -> list[CorpusRecord]:
    """The development rows only, as the lane's rule requires (PRD §9.7, §11.6).

    Every fold is populated: `assign` puts one of mini-50's seven servers in the holdout and
    round-robins the remaining six over five folds. `test_data` asserts that, so a future change
    to the splitter that empties a fold fails loudly instead of quietly making fold-wise tests
    vacuous — which is exactly what the temporary `sha256(server) % 5` helper used to do.
    """
    return [r for r in assigned_mini_50() if r.split == "dev"]
