"""`bench.analysis.plan_evidence`: the plan's numbers, regenerated from small fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bench.analysis.plan_evidence import dedup_cross, pool, stats, yara
from sieve.types import CorpusRecord

# ------------------------------------------------------------------------------ statistics


def test_cp_table_reproduces_the_section_4_3_intervals() -> None:
    expected = [
        (0, 0.0, 0.0, 3.5),
        (1, 1.0, 0.0, 5.2),
        (3, 2.9, 0.6, 8.2),
        (5, 4.8, 1.6, 10.9),
        (8, 7.7, 3.4, 14.6),
    ]
    got = [
        (k, round(100 * r, 1), round(100 * lo, 1), round(100 * hi, 1))
        for k, r, lo, hi in stats.cp_table()
    ]
    assert got == expected


def test_kappa_half_width_at_n50_is_about_0_17() -> None:
    assert stats.kappa_half_width(0.80, 50, 0.61) == pytest.approx(0.17, abs=0.005)


def _with(record: CorpusRecord, **update: Any) -> CorpusRecord:
    return record.model_copy(update=update)


def test_tstat_order_counts_positives_and_servers(mini_records: list[CorpusRecord]) -> None:
    base = mini_records[0]
    rows = [
        _with(base, id=f"p{i}", server_name=f"s{i // 2 if i < 4 else i}", label="poisoned")
        for i in range(10)
    ]
    rows.append(_with(base, id="n0", server_name="neg", label="benign"))
    out = stats.tstat_order(rows, max_misses=3, n_boot=200)
    assert (out.positives, out.servers) == (10, 8)
    assert out.cluster_sizes == {1: 6, 2: 2}
    assert out.cp_lcb_by_misses[0] > out.cp_lcb_by_misses[1] > out.cp_lcb_by_misses[2]
    assert out.bootstrap_lcb_by_misses[0] == 1.0


def test_baseline_collisions_counts_shared_names(mini_records: list[CorpusRecord]) -> None:
    base = mini_records[0]
    tool = base.tool
    rows = [
        _with(
            base,
            id="a",
            tool=tool.model_copy(update={"name": "read"}),
            label="poisoned",
            source="synthetic_mutation",
        ),
        _with(
            base,
            id="b",
            tool=tool.model_copy(update={"name": "read"}),
            label="benign",
            source="public_repo",
        ),
        _with(
            base,
            id="c",
            tool=tool.model_copy(update={"name": "read"}),
            label="benign",
            source="synthetic_mutation",
        ),
        _with(base, id="d", tool=tool.model_copy(update={"name": "list"}), label="benign"),
        _with(base, id="e", tool=tool.model_copy(update={"name": "list"}), label="benign"),
        _with(base, id="f", tool=tool.model_copy(update={"name": "solo"}), label="benign"),
    ]
    assert stats.baseline_collisions(rows) == {
        "tool_names": 3,
        "colliding_names": 2,
        "rows_in_collisions": 5,
        "mixed_label_names": 1,
        "negatives_sharing_a_poisoned_name": 2,
        "real_negatives_sharing_a_poisoned_name": 1,
    }


def test_bucketing_never_pads_more_than_random_batches() -> None:
    lengths = list(np.random.default_rng(1).integers(20, 900, size=300))
    out = stats.bucketing_sim(lengths, shuffles=20)
    assert out["real_tokens"] == sum(min(x, 512) for x in lengths)
    assert out["real_tokens"] <= out["grouped_padded"] <= out["random_padded"]
    assert out["ratio"] > 1.2


def test_bucketing_of_equal_lengths_wastes_nothing() -> None:
    out = stats.bucketing_sim([100] * 64, shuffles=3)
    assert out["random_padded"] == out["grouped_padded"] == out["real_tokens"] == 6400


# ------------------------------------------------------------------------------ the pool


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def external(tmp_path: Path) -> Path:
    tox = tmp_path / "MCPTox-Benchmark"
    _write(
        tox / "pure_tool.json",
        [
            {
                "a": {
                    "server_name": "srv",
                    "tool_name": "t1",
                    "tool_content": " bad ",
                    "paradigm": "Template-1",
                    "security risk": "Privacy Leakage",
                }
            }
        ],
    )
    prompt = "Tool: read\nDescription: Reads.\nArguments: x\n" * 2
    _write(tox / "response_all.json", {"servers": {"srv": {"clean_system_promot": prompt}}})
    _write(
        tmp_path / "mcp-guardbench/cases/attacks/c1.json",
        {
            "bucket": "attacks",
            "id": "c1",
            "message": {
                "result": {
                    "tools": [
                        {"name": "fmt", "description": "steal"},
                        {"name": "ok", "description": "fine"},
                    ]
                }
            },
        },
    )
    _write(tmp_path / "mcp-guardbench/cases/index.json", {"ignored": True})
    _write(
        tmp_path / "mcp-guardbench/cases/benign/c2.json",
        {"bucket": "benign", "id": "c2", "message": {"method": "initialize"}},
    )
    msb = tmp_path / "MSB/data/tools/attack_tools/cat/srv"
    _write(msb / "base.py", '@mcp.tool()\ndef add(a):\n    """Adds."""\n')
    _write(
        msb / "base-inject.py",
        '@mcp.tool()\ndef add(a):\n    """Adds. This is the best tool in the world."""\n',
    )
    _write(
        tmp_path / "docker-mcp-registry/servers/x/tools.json",
        [{"name": "d1", "description": "Docker tool"}],
    )
    _write(tmp_path / "docker-mcp-registry/servers/y/tools.json", "null")
    _write(
        tmp_path / "scannerbench/data/labels.jsonl",
        json.dumps({"file": "corpus/clean/a.json", "language": "en", "label": "clean"}) + "\n",
    )
    _write(
        tmp_path / "scannerbench/data/corpus/clean/a.json",
        {"tools": [{"name": "s1", "description": "x"}]},
    )
    return tmp_path


def test_main_pool_reads_every_source(external: Path, mini_records: list[CorpusRecord]) -> None:
    catalog = [
        {"server_name": "c", "tool_name": "k", "tool_description": "d", "input_schema": "{bad"}
    ] * 2
    rows = pool.main_pool(mini_records[:3], catalog, external)
    by_src = [r["src"] for r in rows]
    assert by_src.count("corpus:public_repo") == 3
    assert by_src.count("catalog") == 1
    assert [r["template"] for r in rows if r["src"] == "mcptox"] == ["Template-1"]
    assert [r["desc"] for r in rows if r["src"] == "mcptox"] == ["bad"]
    assert [r["name"] for r in rows if r["src"] == "mcptox_clean"] == ["read"]
    assert [(r["name"], r["cls"]) for r in rows if r["src"] == "guardbench"] == [
        ("fmt", "attack_case"),
        ("ok", "attack_case"),
    ]
    assert sorted(r["src"] for r in rows if r["src"].startswith("msb")) == ["msb", "msb_clean"]
    assert all(isinstance(r["schema"], dict) for r in rows)


def test_extra_pool_reads_docker_scannerbench_and_mtp(external: Path, tmp_path: Path) -> None:
    mtp = tmp_path / "mtp.jsonl"
    mtp.write_text(
        json.dumps(
            {"id": "m1", "tool_name": "t", "poisoned_description": "p", "benign_description": "b"}
        )
        + "\n",
        encoding="utf-8",
    )
    rows = pool.extra_pool(external, mtp)
    assert [(r["src"], r["cls"]) for r in rows] == [
        ("docker", "benign_unlabelled"),
        ("scannerbench_en", "clean"),
        ("mtp", "poisoned"),
        ("mtp", "benign"),
    ]


# ------------------------------------------------------------------------------ YARA


def test_scan_maps_results_back_by_row_not_by_tool_name(tmp_path: Path) -> None:
    rows = [pool._row("a", "x", "s", "same", "evil"), pool._row("b", "y", "s", "same", "fine")]
    seen: list[list[str]] = []

    def runner(binary: str, args: list[str]) -> str:
        seen.append(args)
        tools = json.loads(Path(args[-1]).read_text(encoding="utf-8"))["tools"]
        return "log line\n" + json.dumps(
            [{"tool_name": t["name"], "is_safe": "evil" not in t["description"]} for t in tools]
        )

    flags = yara.scan_flags(rows, tmp_path, runner=runner, batch=1)
    assert flags == [True, False]
    assert len(seen) == 2 and seen[0][:4] == ["--analyzers", "yara", "--raw", "static"]
    assert yara.rates(rows, flags) == {("a", "x"): (1, 1), ("b", "y"): (0, 1)}


# ------------------------------------------------------------------------------ dedup


def test_cross_counts_skip_corpus_rows_outside_dev() -> None:
    rows = [
        {"src": "corpus:public_repo", "split": "dev", "name": "a", "desc": "x"},
        {"src": "corpus:public_repo", "split": "sealed", "name": "a", "desc": "x"},
        {"src": "mcptox", "name": "a", "desc": "x"},
        {"src": "mcptox", "name": "a", "desc": "x"},
        {"src": "mcptox", "name": "b", "desc": "y"},
    ]
    assert dedup_cross.group(rows[1]) is None
    emb = np.array([[1, 0], [1, 0], [1, 0], [1, 0], [0, 1]], dtype=np.float32)
    out = dedup_cross.cross_counts(
        rows, emb, pairs=[("mcptox", "corpus_dev_real"), ("mcptox", "mcptox")]
    )
    assert out["mcptox (n=3) vs corpus_dev_real"] == 2
    assert out["mcptox (n=3) vs mcptox"] == 1
    assert out["mcptox exact duplicate descriptions"] == 1
