"""Pinned third-party sources for evaluation and plan evidence, fetched into a gitignored folder.

Nothing fetched here is committed or redistributed (training plan §5.1: pointers, not copies).
Every git source is pinned to a full commit, cloned without blobs, checked out detached, and
verified by `rev-parse HEAD`; a checkout that is not at its pin is refused, never silently used.
The clones are read as data only: no file in them is executed (PRD SC-1).
"""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

EXTERNAL_ROOT = Path("bench/data/external")


@dataclass(frozen=True)
class GitPin:
    url: str
    commit: str
    licence: str
    sparse: tuple[str, ...] = ()


#: Commits read on 2026-10-02 (training plan §2.1). Licences as read from each repo's LICENSE.
GIT_PINS: dict[str, GitPin] = {
    "MCPTox-Benchmark": GitPin(
        "https://github.com/zhiqiangwang4/MCPTox-Benchmark",
        "f85189f9ad12504c197c7f920ab818a40657b1fa",
        "none (eval-only, not redistributed)",
    ),
    "mcp-guardbench": GitPin(
        "https://github.com/getmcpm/mcp-guardbench",
        "e879589f1e1abe5421573355616630a62304ecfc",
        "MIT",
    ),
    "MCPSecBench": GitPin(
        "https://github.com/AIS2Lab/MCPSecBench",
        "7612c5a3e811dcf01f64e4f2bb324591a2feaaf4",
        "MIT",
    ),
    "MSB": GitPin(
        "https://github.com/dongsenzhang/MSB",
        "c1d6a70171e4d2c44c87a2ae909d13df00c6aa8d",
        "MIT",
    ),
    "when-mcp-servers-attack": GitPin(
        "https://github.com/zhao-weibo/when-mcp-servers-attack",
        "012843fb08f04fba7278d1d2116b587a8191efba",
        "Apache-2.0",
    ),
    "scannerbench": GitPin(
        "https://github.com/fevziegeyurtsevenler/scannerbench",
        "a8fb60196c91e9b61ae251096bde0d679ff5de88",
        "CC-BY-4.0 (data), Apache-2.0 (code)",
    ),
    "docker-mcp-registry": GitPin(
        "https://github.com/docker/mcp-registry",
        "49b643ce3fc73e6ee80bb962719b6e2990e3d397",
        "MIT (registry); tool text belongs to each server",
        sparse=("servers/*/tools.json",),
    ),
}

#: Hugging Face dataset pins: (repo id, revision, file).
MTP = (
    "fevziegeyurtsevenler/mcp-tool-poisoning",
    "9b7a1088526d424c518d856ba5204f935a279727",
    "mtp.jsonl",
)

Git = Callable[[list[str]], str]


class PinMismatchError(RuntimeError):
    """A checkout is not at the commit it is pinned to."""


def _git(argv: list[str]) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv built from the pins above, no shell
        argv, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return done.stdout


def fetch_git(name: str, *, root: Path = EXTERNAL_ROOT, git: Git = _git) -> Path:
    """Clone `name` at its pin into `root/name` (or reuse a checkout already there)."""
    pin = GIT_PINS[name]
    dest = root / name
    if not dest.exists():
        root.mkdir(parents=True, exist_ok=True)
        git(["git", "clone", "--quiet", "--filter=blob:none", "--no-checkout", pin.url, str(dest)])
    if pin.sparse:
        git(["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", *pin.sparse])
    git(["git", "-C", str(dest), "checkout", "--detach", pin.commit])
    head = git(["git", "-C", str(dest), "rev-parse", "HEAD"]).strip()
    if head != pin.commit:
        raise PinMismatchError(f"{dest} is at {head}, pinned to {pin.commit}")
    return dest


def fetch_mtp() -> Path:
    """The MTP pairs file at its pinned revision (cached by `huggingface_hub`)."""
    from huggingface_hub import hf_hub_download  # the model extra's

    repo, revision, filename = MTP
    return Path(hf_hub_download(repo, filename, repo_type="dataset", revision=revision))


def fetch_all(root: Path = EXTERNAL_ROOT) -> dict[str, Path]:
    paths = {name: fetch_git(name, root=root) for name in GIT_PINS}
    paths["mtp"] = fetch_mtp()
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=EXTERNAL_ROOT)
    args = parser.parse_args(argv)
    for name, path in fetch_all(args.root).items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
