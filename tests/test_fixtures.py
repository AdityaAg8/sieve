import json
from pathlib import Path

import pytest

from sieve.protocols import FakeClassifier
from sieve.serialise import serialise
from sieve.types import LABELS, CorpusRecord, Finding, ScanSummary, ToolDefinition, ToolRecord

FX = Path("fixtures")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_sample_27_has_27_valid_tools_and_labels() -> None:
    d = json.loads(_read(FX / "tools" / "sample-27.json"))
    labels = json.loads(_read(FX / "tools" / "sample-27.labels.json"))
    tools = [ToolDefinition.model_validate(t) for t in d["tools"]]
    assert len(tools) == 27 and len(labels) == 27
    counts = {"benign": 0, "imperative_benign": 0, "poisoned": 0}
    for v in labels.values():
        counts[v["label"]] += 1
    assert counts == {"benign": 13, "imperative_benign": 8, "poisoned": 6}
    names = {n for ns in d["servers"].values() for n in ns}
    assert names == {t.name for t in tools}


def test_mini_corpus_loads() -> None:
    lines = _read(FX / "corpus" / "mini-50.jsonl").splitlines()
    recs = [CorpusRecord.model_validate_json(line) for line in lines]
    assert len(recs) == 50
    assert {r.label for r in recs} == {"benign", "imperative_benign", "poisoned"}
    assert all(r.split == "dev" and r.fold is not None for r in recs)


def test_golden_scanner_output_exists() -> None:
    d = json.loads(_read(FX / "scanner" / "cisco-yara-sample-27.json"))
    assert d, "golden must be non-empty; capture it with mcp-scanner static mode"


def test_fixture_bands_match_fake_classifier() -> None:
    """The three records inlined in tests/test_protocols.py (Task 0.9) mirror this fixture."""
    d = json.loads(_read(FX / "tools" / "sample-27.json"))
    by_name = {t["name"]: t for t in d["tools"]}
    server_of = {n: s for s, ns in d["servers"].items() for n in ns}

    def rec(name: str) -> ToolRecord:
        return ToolRecord(
            id=name,
            server_name=server_of[name],
            tool=ToolDefinition.model_validate(by_name[name]),
            sibling_tool_names=[n for n in d["servers"][server_of[name]] if n != name],
        )

    clf = FakeClassifier()
    band = ("write_file", "read_file", "get_current")
    poisoned = clf.predict_proba([serialise(rec(n)) for n in band])[:, LABELS.index("poisoned")]
    assert poisoned[0] >= clf.thresholds.t_high
    assert clf.thresholds.t_low <= poisoned[1] < clf.thresholds.t_high
    assert poisoned[2] < clf.thresholds.t_low


def test_verdicts_fixture_validates() -> None:
    doc = json.loads(_read(FX / "verdicts" / "sample-27.json"))
    assert doc["meta"]["provisional"] is True
    summary = ScanSummary.model_validate(doc["summary"])
    findings = [Finding.model_validate(f) for f in doc["findings"]]
    assert len(findings) == 27 and summary.tool_count == 27
    counts = {"confirmed": 0, "needs_review": 0, "dismissed": 0}
    for f in findings:
        counts[f.result.verdict] += 1
    assert counts == {"confirmed": 4, "needs_review": 2, "dismissed": 21}
    assert (summary.confirmed, summary.needs_review, summary.dismissed) == (4, 2, 21)
    golden = json.loads(_read(FX / "scanner" / "cisco-yara-sample-27.json"))
    assert summary.scanner_flagged == len([t for t, v in golden.items() if v.get("findings")]), (
        "scanner_flagged must equal the golden's flag count"
    )
    # Every result carries the three-class explanation the UI renders on dismissal (PRD §14.6).
    for f in findings:
        assert set(f.result.class_probabilities) == {"benign", "imperative_benign", "poisoned"}
        assert f.result.predicted_class in f.result.class_probabilities


@pytest.mark.parametrize(
    ("name", "min_attack", "min_clean"),
    [("judge-cases-dev.jsonl", 25, 15), ("judge-cases-holdout.jsonl", 15, 10)],
)
def test_judge_cases(name: str, min_attack: int, min_clean: int) -> None:
    lines = _read(FX / "injection" / name).splitlines()
    cases = [json.loads(line) for line in lines]
    attacks = [c for c in cases if c["kind"] == "attack"]
    cleans = [c for c in cases if c["kind"] == "clean"]
    assert len(attacks) >= min_attack and len(cleans) >= min_clean
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        ToolDefinition.model_validate(case["tool"])
        assert case["kind"] in ("attack", "clean")
        assert case["expected"] == ("not_dismissed" if case["kind"] == "attack" else "dismissed")
        assert ("attack_category" in case) == (case["kind"] == "attack")
