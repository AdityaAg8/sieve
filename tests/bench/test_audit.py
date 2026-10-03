"""The label audits (training plan F3, §4.3): the dev-50 gate and the report-only test-ID audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.label import audit
from sieve.types import CorpusRecord, Label


@pytest.mark.parametrize(
    ("k", "rate", "lo", "hi"),
    [
        (0, 0.0, 0.0, 3.5),
        (1, 1.0, 0.0, 5.2),
        (3, 2.9, 0.6, 8.2),
        (5, 4.8, 1.6, 10.9),
        (8, 7.7, 3.4, 14.6),
    ],
)
def test_the_clopper_pearson_table_of_section_4_3(
    k: int, rate: float, lo: float, hi: float
) -> None:
    point, low, high = audit.clopper_pearson(k, 104)
    assert (round(100 * point, 1), round(100 * low, 1), round(100 * high, 1)) == (rate, lo, hi)


def _row(rid: str, server: str, label: Label, labeller: str, split: str = "dev") -> CorpusRecord:
    return CorpusRecord(
        id=rid,
        source="public_repo",
        source_repo="https://example.invalid/r",
        source_commit="c",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name=server,
        tool={"name": "t", "description": rid, "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,
        labeller=labeller,
        labelled_at="2026-09-21T00:00:00Z",  # type: ignore[arg-type]
        split=split,  # type: ignore[arg-type]
    )


def _dev(tmp_path: Path, disagree: int) -> tuple[Path, Path, list[str]]:
    """60 real dev rows; the 50 audit rows are human-labelled; `disagree` of them disagree."""
    rows = [
        _row(
            f"r{i:03d}",
            f"s{i % 25}",
            "benign" if i % 2 else "imperative_benign",
            "claude-sonnet-5-provisional",
        )
        for i in range(60)
    ]
    from bench.label.sample import dev_audit_ids

    ids = dev_audit_ids(rows)
    majority = {}
    out = []
    for r in rows:
        if r.id in ids:
            human = r.label
            out.append(r.model_copy(update={"labeller": "aditya"}))
            flip = ids.index(r.id) < disagree
            majority[r.id] = (
                ("benign" if human == "imperative_benign" else "imperative_benign")
                if flip
                else human
            )
        else:
            out.append(r)
            majority[r.id] = r.label
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in out), encoding="utf-8", newline="\n"
    )
    major = tmp_path / "majority.json"
    major.write_text(json.dumps(majority), encoding="utf-8")
    return corpus, major, ids


@pytest.mark.parametrize(("disagree", "code"), [(2, 0), (14, audit.STOP)])
def test_the_dev50_gate_passes_and_stops_on_either_side_of_070(
    tmp_path: Path, disagree: int, code: int
) -> None:
    corpus, major, _ = _dev(tmp_path, disagree)
    out = tmp_path / "label_audit.json"
    assert (
        audit.main(
            [
                "--gate",
                "dev50",
                "--corpus",
                str(corpus),
                "--split-corpus",
                str(corpus),
                "--majority",
                str(major),
                "--out",
                str(out),
                "--n-boot",
                "200",
            ]
        )
        == code
    )
    report = json.loads(out.read_text(encoding="utf-8"))["dev50"]
    assert report["n"] == 50
    assert report["passed"] is (code == 0)
    assert report["kappa_lo"] <= report["kappa"] <= report["kappa_hi"]


def test_the_gate_waits_for_all_fifty_human_labels(tmp_path: Path) -> None:
    corpus, major, ids = _dev(tmp_path, 0)
    rows = [
        CorpusRecord.model_validate_json(x) for x in corpus.read_text(encoding="utf-8").splitlines()
    ]
    rows = [
        r.model_copy(update={"labeller": "claude-sonnet-5-provisional"}) if r.id == ids[0] else r
        for r in rows
    ]
    corpus.write_text(
        "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8", newline="\n"
    )
    code = audit.main(
        [
            "--gate",
            "dev50",
            "--corpus",
            str(corpus),
            "--split-corpus",
            str(corpus),
            "--majority",
            str(major),
            "--out",
            str(tmp_path / "a.json"),
        ]
    )
    assert code == audit.NOT_READY


def test_the_test_id_report_is_report_only_with_both_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gold = {f"h{i:03d}": ("benign" if i % 3 else "imperative_benign") for i in range(104)}
    servers = {rid: f"srv{i % 30}" for i, rid in enumerate(gold)}
    majority = dict(gold)
    for rid in list(gold)[:3]:
        majority[rid] = "imperative_benign" if gold[rid] == "benign" else "benign"
    majority[list(gold)[3]] = None  # undecided by the ensemble: counted as an error
    report = audit.test_id_report(gold, majority, servers, n_boot=300)
    assert report["n"] == 104 and report["errors"] == 4 and report["undecided"] == 1
    assert round(100 * report["error_rate"], 1) == 3.8
    assert report["cp_lo"] <= report["error_rate"] <= report["cp_hi"]
    assert report["cluster_lo"] <= report["error_rate"] <= report["cluster_hi"]
    assert report["kappa_lo"] <= report["kappa"] <= report["kappa_hi"]
    assert report["report_only"] is True
