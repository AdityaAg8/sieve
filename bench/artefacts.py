"""Supply, and digest-verify, the trained artefacts the reproduction gate needs.

`models/` is git-ignored, so a clean clone can never carry a trained artefact. That is the
right layout -- a few hundred megabytes of weights do not belong in git -- but it means the
clean-clone reproduction (`tests/integration/test_make_bench_clean_clone.sh`) has to be
*supplied* with them rather than find them. `bench/artefacts.json` is the manifest that says
where they come from and what they must hash to; this module reads it, fetches them, and
refuses anything whose bytes do not match.

Two callers, one contract:

* the weekly workflow runs ``python -m bench.artefacts --dest <dir>``, which downloads each
  artefact at its pinned revision and verifies every listed digest;
* a human runs the reproduction against a directory they already have, by pointing
  ``SIEVE_ARTEFACTS_DIR`` at it -- no manifest, no network.

**A pending manifest is a skip, never a pass.** Until a human has pushed the artefacts and
filled in the repo ids, revisions and digests, every entry carries ``null`` and this module
exits 3: "artefact missing, nothing was reproduced". Inventing a revision or a digest here
would make the verification check a fiction, which is worse than not checking at all.

This is also what closes THREAT_MODEL.md's residual risk 9 for the reproduction path: the Hub
revision is pinned and the bytes are hashed, rather than "whatever the Hub served today".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("bench.artefacts")

MANIFEST = Path("bench/artefacts.json")

#: The six artefact directories ablation rows 3 and 4 name, across the default three seeds.
#: `bench/ablations.yaml` spells them `models/deberta-v3-base{,-desc}-s*`.
REQUIRED: tuple[str, ...] = (
    "deberta-v3-base-s0",
    "deberta-v3-base-s1",
    "deberta-v3-base-s2",
    "deberta-v3-base-desc-s0",
    "deberta-v3-base-desc-s1",
    "deberta-v3-base-desc-s2",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Exit code for "a prerequisite is not available, so nothing was measured". Shared with
#: `tests/integration/test_make_bench_clean_clone.sh`, whose header documents the contract.
ARTEFACT_MISSING = 3


class ManifestError(ValueError):
    """The manifest is not usable: a bad shape, a half-filled entry, or a bad digest string."""


@dataclass(frozen=True)
class Artefact:
    """One artefact directory, as the manifest describes it."""

    name: str
    hub_repo: str | None
    revision: str | None
    files: Mapping[str, str]

    @property
    def pending(self) -> bool:
        """True while a human has not filled this entry in. Both halves move together."""
        return self.hub_repo is None or self.revision is None


def _entry(name: str, raw: object) -> Artefact:
    if not isinstance(raw, dict):
        raise ManifestError(f"{name}: expected an object, got {type(raw).__name__}")
    unknown = set(raw) - {"hub_repo", "revision", "files"}
    if unknown:
        raise ManifestError(f"{name}: unknown key(s) {sorted(unknown)}")
    repo, revision, files = raw.get("hub_repo"), raw.get("revision"), raw.get("files", {})
    for key, value in (("hub_repo", repo), ("revision", revision)):
        if value is not None and not (isinstance(value, str) and value.strip()):
            raise ManifestError(f"{name}: {key} must be a non-empty string or null")
    # Half-filled is the dangerous state: a repo with no revision downloads whatever the Hub
    # serves today, which is exactly the risk pinning exists to remove.
    if (repo is None) != (revision is None):
        raise ManifestError(f"{name}: hub_repo and revision must be filled in together")
    if not isinstance(files, dict):
        raise ManifestError(f"{name}: files must be an object of path -> sha256")
    for path, digest in files.items():
        if not isinstance(digest, str) or not _SHA256.match(digest):
            raise ManifestError(f"{name}: {path} must map to a lowercase hex sha256")
        if Path(path).is_absolute() or ".." in Path(path).parts:
            raise ManifestError(f"{name}: {path} must be a relative path inside the artefact")
    if repo is not None and not files:
        raise ManifestError(f"{name}: a filled-in entry must list at least one file to verify")
    return Artefact(name=name, hub_repo=repo, revision=revision, files=dict(files))


def load_manifest(path: Path = MANIFEST) -> dict[str, Artefact]:
    """Parse and validate the manifest. Raises `ManifestError` rather than returning junk."""
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict) or not isinstance(raw.get("artefacts"), dict):
        raise ManifestError(f"{path}: expected a top-level object with an `artefacts` object")
    manifest = {name: _entry(name, value) for name, value in raw["artefacts"].items()}
    missing = [name for name in REQUIRED if name not in manifest]
    if missing:
        raise ManifestError(f"{path}: no entry for {missing}")
    return manifest


def pending(manifest: Mapping[str, Artefact], names: Iterable[str] = REQUIRED) -> list[str]:
    """The artefacts a human still has to fill in. Empty means the manifest is usable."""
    return [name for name in names if name not in manifest or manifest[name].pending]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(root: Path, artefact: Artefact) -> list[str]:
    """Every problem with `root/<artefact.name>`; empty means the bytes are the pinned ones."""
    problems: list[str] = []
    directory = root / artefact.name
    if not directory.is_dir():
        return [f"{artefact.name}: no such directory under {root}"]
    for relative, expected in sorted(artefact.files.items()):
        target = directory / relative
        if not target.is_file():
            problems.append(f"{artefact.name}/{relative}: missing")
            continue
        actual = sha256_of(target)
        if actual != expected:
            problems.append(f"{artefact.name}/{relative}: sha256 {actual}, expected {expected}")
    return problems


def verify_all(root: Path, manifest: Mapping[str, Artefact]) -> list[str]:
    """Verify every non-pending required artefact under `root`."""
    problems: list[str] = []
    for name in REQUIRED:
        artefact = manifest[name]
        if artefact.pending:
            problems.append(f"{name}: pending; the manifest has no revision for it")
            continue
        problems.extend(verify(root, artefact))
    return problems


def download(artefact: Artefact, dest: Path) -> Path:
    """`snapshot_download` the artefact at its pinned revision into `dest/<name>`.

    Imported lazily: `huggingface_hub` is in the `model` extra, and this module is imported by
    tests that have no business needing it.
    """
    if artefact.pending:  # pragma: no cover -- callers check `pending()` first
        raise ManifestError(f"{artefact.name}: pending, nothing to download")
    from huggingface_hub import snapshot_download

    target = dest / artefact.name
    target.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=artefact.hub_repo,
        revision=artefact.revision,
        local_dir=str(target),
    )
    return target


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m bench.artefacts --dest <dir>`. 0 supplied and verified, 3 pending, 1 bad bytes."""
    parser = argparse.ArgumentParser(prog="python -m bench.artefacts")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--dest", type=Path, required=True, help="where models/ is assembled")
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="check the digests of a directory already on disk; download nothing",
    )
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    manifest = load_manifest(ns.manifest)
    waiting = pending(manifest)
    if waiting:
        log.warning(
            "artefact missing: %d of %d entries in %s are pending (%s). Nothing was fetched and "
            "nothing can be reproduced; a human fills these in after the Hub push.",
            len(waiting),
            len(REQUIRED),
            ns.manifest,
            ", ".join(waiting),
        )
        return ARTEFACT_MISSING

    if not ns.verify_only:
        for name in REQUIRED:
            log.info("fetching %s at %s", manifest[name].hub_repo, manifest[name].revision)
            download(manifest[name], ns.dest)

    problems = verify_all(ns.dest, manifest)
    if problems:
        for problem in problems:
            log.error("%s", problem)
        return 1
    log.info("%d artefacts verified against %s", len(REQUIRED), ns.manifest)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
