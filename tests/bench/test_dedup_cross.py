"""Eval-vs-dev dedup (training plan G4, §5.2): exact, vs-dev and internal-twin removal."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bench.split import dedup_cross as dc


class TableEmbedder:
    """Each text maps to a fixed unit vector: cosines are chosen by the test, not a model."""

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.array([self.table[t] for t in texts], dtype=np.float64)
        return out / np.linalg.norm(out, axis=1, keepdims=True)


def _item(rid: str, desc: str, name: str = "t") -> dc.Item:
    return dc.Item(id=rid, name=name, description=desc)


def test_each_rule_removes_what_it_should_and_counts_are_on_the_raw_set() -> None:
    items = [
        _item("a", "Reads a file."),
        _item("b", "reads  a FILE."),  # exact duplicate of a (NFKC / case / whitespace)
        _item("c", "Near dev."),  # cosine 0.95 to a dev row
        _item("d", "Twin one."),
        _item("e", "Twin two."),  # cosine 0.99 to d: one internal component
        _item("f", "Alone."),
    ]
    dev = [_item("dev-1", "A dev row."), _item("dev-2", "Alone.")]  # f is an exact dev twin
    v = {
        "t. Reads a file.": [1, 0, 0, 0, 0],
        "t. reads  a FILE.": [1, 0, 0, 0, 0],
        "t. Near dev.": [0, 1, 0, 0, 0],
        "t. Twin one.": [0, 0, 1, 0, 0],
        "t. Twin two.": [0, 0, 1, 0.14, 0],
        "t. Alone.": [0, 0, 0, 0, 1],
        "t. A dev row.": [0, 1, 0, 0.33, 0],
    }
    report = dc.dedup_set(items, dev, embedder=TableEmbedder(v))
    assert report.exact_within == 1
    assert report.vs_dev == 2  # c (near) and f (exact, and near)
    assert (report.vs_dev_near, report.vs_dev_exact) == (2, 1)
    assert report.internal_twin_rows == 2  # a (exact twin b) and d (near twin e)
    assert report.internal_twin_groups == 2  # {a, b} and {d, e}, on the raw set
    assert set(report.dropped) == {"b", "c", "f", "e"}
    assert report.surviving == 2


def test_a_report_only_set_loses_only_its_dev_twins() -> None:
    items = [_item("a", "Same."), _item("b", "same."), _item("c", "Near dev.")]
    v = {"t. Same.": [1, 0], "t. same.": [1, 0], "t. Near dev.": [0, 1], "t. Dev.": [0, 1]}
    report = dc.dedup_set(items, [_item("d", "Dev.")], embedder=TableEmbedder(v), full=False)
    assert report.exact_within == 1 and report.internal_twin_rows == 1  # disclosed
    assert report.dropped == ["c"] and report.removal == "dev_twins_only"


def test_a_chain_component_keeps_exactly_one_row() -> None:
    items = [_item("x", "1"), _item("y", "2"), _item("z", "3")]
    v = {"t. 1": [1, 0, 0], "t. 2": [0.94, 0.34, 0], "t. 3": [0.8, 0.6, 0]}
    report = dc.dedup_set(items, [], embedder=TableEmbedder(v))
    # x~y (0.94) and y~z (0.95) chain; x~z is 0.8: one component, so one survivor.
    assert report.dropped == ["y", "z"] and report.surviving == 1


def test_the_report_writes_every_count_without_ranges(tmp_path: Path) -> None:
    items = {"docker": [_item("a", "A"), _item("b", "B")], "mtp": [_item("m", "A")]}
    v = {"t. A": [1, 0], "t. B": [0, 1]}
    out = tmp_path / "dedup_cross.json"
    dc.write_report(dc.run_sets(items, [], embedder=TableEmbedder(v)), out)
    report = json.loads(out.read_text(encoding="utf-8"))
    entry = report["sets"]["docker"]
    assert entry["raw"] == 2 and entry["surviving"] == 2 and entry["dropped"] == []
    assert all(isinstance(entry[k], int) for k in dc.COUNT_KEYS)
    assert report["threshold"] == 0.92
    assert report["sets"]["mtp"]["removal"] == "dev_twins_only"


@pytest.mark.slow
@pytest.mark.skipif(
    not Path("bench/data/external/docker-mcp-registry").exists()
    or not Path("bench/data/labelled.jsonl").exists(),
    reason="the pinned upstream clones or the corpus are not present",
)
def test_the_real_counts() -> None:
    report = dc.run_default()
    sets = report["sets"]
    assert sets["mcptox_clean"]["exact_within"] == 35
    assert sets["mcptox_clean"]["vs_dev_near"] == 77
    assert sets["real_poisoned"]["internal_twin_rows"] == 2
    assert sets["real_poisoned"]["surviving"] == 483
    assert sets["docker"]["internal_twin_rows"] == 33
