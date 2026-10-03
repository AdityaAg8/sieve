"""`bench.collect.external`: pinned third-party sources, fetched into a gitignored folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.collect import external


class FakeGit:
    """Records every git argv; `rev-parse HEAD` answers with `head`."""

    def __init__(self, head: str) -> None:
        self.head = head
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[-2:] == ["rev-parse", "HEAD"]:
            return self.head + "\n"
        if argv[1] == "clone":
            Path(argv[-1]).mkdir(parents=True)
        return ""


def test_every_git_pin_is_a_full_commit_hash() -> None:
    for name, pin in external.GIT_PINS.items():
        assert len(pin.commit) == 40, name
        assert pin.url.startswith("https://github.com/"), name


def test_fetch_clones_checks_out_the_pin_and_verifies_head(tmp_path: Path) -> None:
    pin = external.GIT_PINS["mcp-guardbench"]
    git = FakeGit(pin.commit)
    dest = external.fetch_git("mcp-guardbench", root=tmp_path, git=git)
    assert dest == tmp_path / "mcp-guardbench"
    assert git.calls[0][:2] == ["git", "clone"] and pin.url in git.calls[0]
    assert ["git", "-C", str(dest), "checkout", "--detach", pin.commit] in git.calls


def test_fetch_refuses_a_head_that_is_not_the_pin(tmp_path: Path) -> None:
    git = FakeGit("0" * 40)
    with pytest.raises(external.PinMismatchError):
        external.fetch_git("mcp-guardbench", root=tmp_path, git=git)


def test_an_existing_checkout_at_the_pin_is_not_cloned_again(tmp_path: Path) -> None:
    pin = external.GIT_PINS["scannerbench"]
    (tmp_path / "scannerbench").mkdir()
    git = FakeGit(pin.commit)
    external.fetch_git("scannerbench", root=tmp_path, git=git)
    assert not any(call[1] == "clone" for call in git.calls)


def test_sparse_pins_set_their_sparse_paths_before_checkout(tmp_path: Path) -> None:
    pin = external.GIT_PINS["docker-mcp-registry"]
    assert pin.sparse
    git = FakeGit(pin.commit)
    external.fetch_git("docker-mcp-registry", root=tmp_path, git=git)
    verbs = [c[3] for c in git.calls if c[1] == "-C"]
    assert verbs.index("sparse-checkout") < verbs.index("checkout")
