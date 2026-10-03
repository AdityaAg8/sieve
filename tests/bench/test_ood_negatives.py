"""Test-OOD negatives (training plan G2): Docker and MCPTox clean as binary eval rows."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from bench import eval_final
from bench.collect import ood_negatives as ood
from bench.collect.ood_rows import OodRow, load_rows
from bench.collect.real_poisoned import hash_server_name
from sieve.types import CorpusRecord

EXTERNAL = Path("bench/data/external")


def _git(root: Path) -> None:
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "pin"]):
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],  # noqa: S607
            cwd=root,
            check=True,
        )


def _docker(root: Path) -> Path:
    for server, tools in {
        "alpha": [
            {"name": "a1", "description": "Lists files.", "inputSchema": {"type": "object"}},
            {"name": "a2", "description": "Reads a file."},
        ],
        "beta": [],
        "gamma": [
            {
                "name": "g1",
                "description": "Searches the web.",
                "arguments": [
                    {"name": "q", "type": "string", "desc": "Query"},
                    {"name": "n", "type": "integer", "desc": "Count", "optional": True},
                ],
            }
        ],
    }.items():
        (root / "servers" / server).mkdir(parents=True)
        (root / "servers" / server / "tools.json").write_text(json.dumps(tools), encoding="utf-8")
    _git(root)
    return root


def _mcptox(root: Path) -> Path:
    prompt = (
        "Tool: read_file\nDescription: Reads a file from disk.\nArguments: path\n"
        "Tool: read_file\nDescription: Reads a file from disk.\nArguments: path\n"
        "Tool: list_dir\nDescription: Lists a directory.\nArguments: path\n"
    )
    root.mkdir(parents=True)
    payload = {"servers": {"Filesystem": {"clean_system_promot": prompt}}}
    (root / "response_all.json").write_text(json.dumps(payload), encoding="utf-8")
    _git(root)
    return root


def _head(root: Path) -> str:
    return subprocess.run(  # noqa: S603
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def test_docker_rows_are_binary_negatives_with_provenance(tmp_path: Path) -> None:
    root = _docker(tmp_path / "docker")
    rows = ood.docker_rows(root, commit=_head(root))
    assert [r.id for r in rows] == ["ood-docker-0000", "ood-docker-0001", "ood-docker-0002"]
    assert [r.server_name for r in rows] == ["alpha", "alpha", "gamma"]
    assert all(isinstance(r, OodRow) and r.poisoned is False for r in rows)
    assert {r.source for r in rows} == {"docker"}
    assert rows[1].tool["inputSchema"] == {"type": "object", "properties": {}}
    assert rows[0].provenance["commit"] == _head(root)
    assert rows[0].provenance["path"] == "servers/alpha/tools.json"


def test_docker_arguments_become_an_input_schema(tmp_path: Path) -> None:
    root = _docker(tmp_path / "docker")
    tool = ood.docker_rows(root, commit=_head(root))[2].tool
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {
            "q": {"type": "string", "description": "Query"},
            "n": {"type": "integer", "description": "Count"},
        },
        "required": ["q"],
    }
    assert "arguments" not in tool


def test_mcptox_clean_is_hashed_like_the_real_poisoned_slice(tmp_path: Path) -> None:
    root = _mcptox(tmp_path / "mcptox")
    rows = ood.mcptox_clean_rows(root, commit=_head(root))
    assert [r.tool["name"] for r in rows] == ["read_file", "list_dir"]  # unique per (server, tool)
    assert {r.server_name for r in rows} == {hash_server_name("Filesystem")}
    assert {r.source for r in rows} == {"mcptox_clean"} and not any(r.poisoned for r in rows)


def test_a_clone_off_its_pin_is_refused(tmp_path: Path) -> None:
    root = _docker(tmp_path / "docker")
    with pytest.raises(ood.PinMismatchError):
        ood.docker_rows(root, commit="0" * 40)


def test_the_yara_sidecar_is_keyed_by_row_id_not_tool_name(tmp_path: Path) -> None:
    root = _docker(tmp_path / "docker")
    rows = ood.docker_rows(root, commit=_head(root))

    def runner(binary: str, args: list[str]) -> str:
        tools = json.loads(Path(args[-1]).read_text(encoding="utf-8"))["tools"]
        return json.dumps(
            [{"tool_name": t["name"], "is_safe": "Searches" not in t["description"]} for t in tools]
        )

    flags = ood.yara_sidecar(rows, tmp_path / "scan", runner=runner)
    assert flags == {"ood-docker-0000": False, "ood-docker-0001": False, "ood-docker-0002": True}


def test_collect_writes_rows_sidecar_and_counts(tmp_path: Path) -> None:
    docker, mcptox = _docker(tmp_path / "docker"), _mcptox(tmp_path / "mcptox")
    pins = {"docker": _head(docker), "mcptox_clean": _head(mcptox)}
    out = tmp_path / "ood"
    counts = ood.collect(
        docker=docker, mcptox=mcptox, out=out, pins=pins, runner=lambda b, a: "[]", yara=False
    )
    assert counts == {"docker": 3, "mcptox_clean": 2}
    rows = load_rows(out / ood.ROWS_FILE)
    assert len({r.id for r in rows}) == 5
    assert not (out / ood.YARA_FILE).exists()


def _record(rid: str, source: str, label: str) -> CorpusRecord:
    return CorpusRecord(
        id=rid,
        source=source,  # type: ignore[arg-type]
        source_repo="https://example.invalid/r",
        source_commit="c",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name="s",
        tool={"name": "t", "description": rid, "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,  # type: ignore[arg-type]
        labeller="aditya",
        labelled_at="2026-09-21T00:00:00Z",  # type: ignore[arg-type]
        split="eval_only",
    )


def test_negatives_never_enter_m13(tmp_path: Path) -> None:
    """M13 is keyed on `source == "real_poisoned"`; an OOD negative, or any other row that
    landed in the eval-only split, is never part of it."""
    root = _docker(tmp_path / "docker")
    negatives = ood.docker_rows(root, commit=_head(root))
    stray = _record("stray", "public_repo", "poisoned")
    real = _record("rp-1", "real_poisoned", "poisoned")
    assert eval_final.m13_rows([*negatives, stray, real]) == [real]


def test_no_ood_source_can_pose_as_the_real_poisoned_slice() -> None:
    assert "real_poisoned" not in ood.SOURCES


@pytest.mark.skipif(
    not (EXTERNAL / "docker-mcp-registry").exists() or not (EXTERNAL / "MCPTox-Benchmark").exists(),
    reason="the pinned upstream clones are not present",
)
def test_the_real_counts_before_dedup() -> None:
    pins = ood.load_pins()
    docker = ood.docker_rows(EXTERNAL / "docker-mcp-registry", commit=pins["docker"].commit)
    clean = ood.mcptox_clean_rows(EXTERNAL / "MCPTox-Benchmark", commit=pins["mcptox_clean"].commit)
    assert (len(docker), len(clean)) == (755, 362)
    poisoned_servers = {
        hash_server_name(t["server_name"])
        for group in json.loads(
            (EXTERNAL / "MCPTox-Benchmark/pure_tool.json").read_text(encoding="utf-8")
        )
        for t in group.values()
    }
    assert {r.server_name for r in clean} == poisoned_servers  # the clusters align
