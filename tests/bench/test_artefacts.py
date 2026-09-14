"""The artefact manifest: its shape, its pending state, and the digests it verifies.

No network anywhere. `download()` is the only function that touches the Hub and it is not
exercised here -- everything this gate decides (pending or not, bytes match or not) is decided
before a download and can be decided over a directory on disk.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.artefacts import (
    ARTEFACT_MISSING,
    MANIFEST,
    REQUIRED,
    Artefact,
    ManifestError,
    load_manifest,
    main,
    pending,
    sha256_of,
    verify,
    verify_all,
)

FILLED = {
    "artefacts": {
        name: {
            "hub_repo": "example/sieve-triage",
            "revision": "0" * 40,
            "files": {"sieve_config.json": "0" * 64},
        }
        for name in REQUIRED
    }
}


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def _artefact_dir(root: Path, name: str, body: bytes) -> str:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sieve_config.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


# ------------------------------------------------------------------ the committed manifest


def test_the_committed_manifest_parses_and_names_every_required_artefact() -> None:
    manifest = load_manifest(MANIFEST)
    assert set(REQUIRED) <= set(manifest)


def test_the_committed_manifest_is_pending_so_the_gate_skips() -> None:
    """Shipped empty on purpose: a human fills it in after the Hub push, and never an agent."""
    assert pending(load_manifest(MANIFEST)) == list(REQUIRED)


def test_a_pending_run_exits_artefact_missing_and_downloads_nothing(tmp_path: Path) -> None:
    assert main(["--dest", str(tmp_path)]) == ARTEFACT_MISSING
    assert list(tmp_path.iterdir()) == [], "a pending manifest must not create anything"


# ------------------------------------------------------------------------------- validation


def test_a_repo_without_a_revision_is_refused(tmp_path: Path) -> None:
    """The dangerous half-filled state: it downloads whatever the Hub serves today."""
    payload = json.loads(json.dumps(FILLED))
    payload["artefacts"][REQUIRED[0]]["revision"] = None
    with pytest.raises(ManifestError, match="filled in together"):
        load_manifest(_write(tmp_path / "m.json", payload))


def test_a_filled_entry_that_lists_no_file_is_refused(tmp_path: Path) -> None:
    """A pinned revision nobody hashes is risk 9 again, with extra steps."""
    payload = json.loads(json.dumps(FILLED))
    payload["artefacts"][REQUIRED[0]]["files"] = {}
    with pytest.raises(ManifestError, match="at least one file"):
        load_manifest(_write(tmp_path / "m.json", payload))


def test_a_digest_that_is_not_a_sha256_is_refused(tmp_path: Path) -> None:
    payload = json.loads(json.dumps(FILLED))
    payload["artefacts"][REQUIRED[0]]["files"] = {"sieve_config.json": "DEADBEEF"}
    with pytest.raises(ManifestError, match="lowercase hex sha256"):
        load_manifest(_write(tmp_path / "m.json", payload))


def test_a_path_that_escapes_the_artefact_directory_is_refused(tmp_path: Path) -> None:
    payload = json.loads(json.dumps(FILLED))
    payload["artefacts"][REQUIRED[0]]["files"] = {"../../etc/passwd": "0" * 64}
    with pytest.raises(ManifestError, match="relative path inside"):
        load_manifest(_write(tmp_path / "m.json", payload))


def test_a_missing_required_artefact_is_refused(tmp_path: Path) -> None:
    payload = json.loads(json.dumps(FILLED))
    del payload["artefacts"][REQUIRED[-1]]
    with pytest.raises(ManifestError, match="no entry for"):
        load_manifest(_write(tmp_path / "m.json", payload))


# ---------------------------------------------------------------------------- verification


def test_verify_accepts_a_directory_whose_bytes_are_the_pinned_ones(tmp_path: Path) -> None:
    digest = _artefact_dir(tmp_path, "a", b'{"model_version": "x"}')
    artefact = Artefact("a", "example/x", "0" * 40, {"sieve_config.json": digest})
    assert verify(tmp_path, artefact) == []


def test_verify_reports_a_single_changed_byte(tmp_path: Path) -> None:
    digest = _artefact_dir(tmp_path, "a", b'{"model_version": "x"}')
    _artefact_dir(tmp_path, "a", b'{"model_version": "y"}')
    artefact = Artefact("a", "example/x", "0" * 40, {"sieve_config.json": digest})
    problems = verify(tmp_path, artefact)
    assert len(problems) == 1
    assert "expected " + digest in problems[0]


def test_verify_reports_a_missing_file_and_a_missing_directory(tmp_path: Path) -> None:
    artefact = Artefact("a", "example/x", "0" * 40, {"sieve_config.json": "0" * 64})
    assert verify(tmp_path, artefact) == ["a: no such directory under " + str(tmp_path)]
    (tmp_path / "a").mkdir()
    assert verify(tmp_path, artefact) == ["a/sieve_config.json: missing"]


def test_verify_all_names_every_pending_entry_rather_than_passing_them(tmp_path: Path) -> None:
    problems = verify_all(tmp_path, load_manifest(MANIFEST))
    assert len(problems) == len(REQUIRED)
    assert all("pending" in problem for problem in problems)


def test_sha256_of_matches_hashlib(tmp_path: Path) -> None:
    body = b"a" * (1 << 20) + b"tail"  # more than one read chunk
    path = tmp_path / "big.bin"
    path.write_bytes(body)
    assert sha256_of(path) == hashlib.sha256(body).hexdigest()


def test_verify_only_checks_a_supplied_directory_without_downloading(tmp_path: Path) -> None:
    """The `--verify-only` path a human takes when they already hold the artefacts."""
    manifest = tmp_path / "m.json"
    dest = tmp_path / "models"
    payload: dict = {"artefacts": {}}
    for name in REQUIRED:
        digest = _artefact_dir(dest, name, f'{{"name": "{name}"}}'.encode())
        payload["artefacts"][name] = {
            "hub_repo": "example/sieve-triage",
            "revision": "0" * 40,
            "files": {"sieve_config.json": digest},
        }
    _write(manifest, payload)

    assert main(["--manifest", str(manifest), "--dest", str(dest), "--verify-only"]) == 0

    (dest / REQUIRED[0] / "sieve_config.json").write_bytes(b"tampered")
    assert main(["--manifest", str(manifest), "--dest", str(dest), "--verify-only"]) == 1
