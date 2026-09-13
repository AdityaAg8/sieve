"""The eval-only `real_poisoned` slice, built from already-published artefacts (SC-3)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from bench.collect.real_poisoned import (
    ArtefactHashMismatch,
    ArtefactSource,
    Pointer,
    collect,
    reconstruct,
)

DATA = Path("tests/bench/data/real_poisoned")
HASHED = re.compile(r"^hashed:[0-9a-f]{8}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mcptox() -> ArtefactSource:
    path = DATA / "mcptox_sample.json"
    return ArtefactSource(
        url="https://example.invalid/mcptox/poisoned_tools.json",
        licence="MIT",
        redistributable=True,
        sha256=_sha256(path),
        path=path,
    )


def _invariant(*, redistributable: bool = True) -> ArtefactSource:
    path = DATA / "invariant_sample.md"
    return ArtefactSource(
        url="https://example.invalid/labs/2025-04-tool-poisoning.md",
        licence="CC-BY-4.0" if redistributable else "All rights reserved",
        redistributable=redistributable,
        sha256=_sha256(path),
        path=path,
    )


def test_collect_builds_eval_only_poisoned_records(tmp_path: Path) -> None:
    out = tmp_path / "real_poisoned.jsonl"
    records = collect([_mcptox(), _invariant()], out=out)
    assert len(records) == 4
    for record in records:
        assert record.source == "real_poisoned"
        assert record.capture_method == "published_artifact"
        assert record.split == "eval_only"
        assert record.fold is None
        assert record.label == "poisoned"
        assert record.labeller == "aditya"
        assert record.source_repo and record.source_license
        assert record.source_commit


def test_server_names_are_hashed_and_the_original_never_survives(tmp_path: Path) -> None:
    records = collect([_mcptox(), _invariant()], out=tmp_path / "out.jsonl")
    for record in records:
        assert HASHED.match(record.server_name), record.server_name
    blob = "".join(r.model_dump_json() for r in records)
    for original in ("acme-notes-server", "acme-mail-relay", "widget-directory-server"):
        assert original not in blob


def test_the_written_file_carries_the_same_records(tmp_path: Path) -> None:
    from sieve.types import CorpusRecord

    out = tmp_path / "out.jsonl"
    records = collect([_mcptox()], out=out)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert [CorpusRecord.model_validate_json(line).id for line in lines] == [r.id for r in records]
    assert b"\r\n" not in out.read_bytes()


def test_tool_text_actually_made_it_across(tmp_path: Path) -> None:
    records = collect([_mcptox()], out=tmp_path / "out.jsonl")
    save_note = next(r for r in records if r.tool.name == "save_note")
    assert "~/.ssh/id_rsa" in (save_note.tool.description or "")
    assert save_note.tool.inputSchema["properties"]["title"]["type"] == "string"


def test_a_markdown_artefact_yields_its_fenced_tool_definitions(tmp_path: Path) -> None:
    records = collect([_invariant()], out=tmp_path / "out.jsonl")
    assert {r.tool.name for r in records} == {"lookup_widget", "list_widgets"}


def test_a_non_redistributable_source_commits_a_pointer_and_no_text(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    pointers = tmp_path / "pointers.json"
    records = collect([_mcptox(), _invariant(redistributable=False)], out=out, pointers=pointers)
    assert {r.tool.name for r in records} == {"save_note", "send_message"}

    on_disk = out.read_text(encoding="utf-8")
    assert "lookup_widget" not in on_disk
    assert "widget-directory" not in on_disk

    entries = json.loads(pointers.read_text(encoding="utf-8"))
    assert len(entries) == 1
    entry = entries[0]
    assert set(entry) == {"url", "sha256", "licence", "extract"}
    assert set(entry["extract"]) == {"path", "json_pointer"}
    assert entry["url"] == "https://example.invalid/labs/2025-04-tool-poisoning.md"
    # The pointer records where to look, never the text itself.
    assert "lookup_widget" not in json.dumps(entries)


def test_reconstruct_rebuilds_the_slice_from_a_verified_cache(tmp_path: Path) -> None:
    source = _invariant(redistributable=False)
    cache = tmp_path / "cache"
    cache.mkdir()
    body = (DATA / "invariant_sample.md").read_bytes()
    (cache / "invariant_sample.md").write_bytes(body)
    pointer = Pointer(
        url=source.url,
        sha256=source.sha256,
        licence=source.licence,
        extract={"path": "invariant_sample.md", "json_pointer": ""},
    )
    records = reconstruct([pointer], cache)
    assert {r.tool.name for r in records} == {"lookup_widget", "list_widgets"}
    assert all(r.split == "eval_only" and r.source == "real_poisoned" for r in records)


def test_reconstruct_refuses_when_the_bytes_do_not_match(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "invariant_sample.md").write_bytes(b"tampered")
    pointer = Pointer(
        url="https://example.invalid/labs/2025-04-tool-poisoning.md",
        sha256=_sha256(DATA / "invariant_sample.md"),
        licence="All rights reserved",
        extract={"path": "invariant_sample.md", "json_pointer": ""},
    )
    with pytest.raises(ArtefactHashMismatch):
        reconstruct([pointer], cache)


def test_reconstruct_refuses_when_the_cache_is_missing(tmp_path: Path) -> None:
    pointer = Pointer(
        url="https://example.invalid/x.md",
        sha256="0" * 64,
        licence="All rights reserved",
        extract={"path": "missing.md", "json_pointer": ""},
    )
    with pytest.raises(FileNotFoundError):
        reconstruct([pointer], tmp_path)


def test_the_committed_pointers_file_stores_no_tool_text() -> None:
    path = Path("bench/data/real_poisoned.pointers.json")
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(entries, list)
    for entry in entries:
        assert set(entry) == {"url", "sha256", "licence", "extract"}
        assert set(entry["extract"]) == {"path", "json_pointer"}
    assert b"\r\n" not in path.read_bytes()


def test_ids_are_stable_across_runs(tmp_path: Path) -> None:
    first = collect([_mcptox()], out=tmp_path / "a.jsonl")
    second = collect([_mcptox()], out=tmp_path / "b.jsonl")
    assert [r.id for r in first] == [r.id for r in second]
