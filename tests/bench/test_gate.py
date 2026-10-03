"""`make gate` (training plan E4, §5.3): the pre-registered gate, one tightening pass, then stop."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from bench import gate
from sieve.types import CorpusRecord, Label

SYN = "synthetic_mutation"
WORDS = ["alpha", "beta", "gamma", "delta", "eps", "zeta", "eta", "theta"]


def _record(
    rid: str, label: Label, text: str, source: str, server: str, host: str | None
) -> CorpusRecord:
    return CorpusRecord(
        id=rid,
        source=source,  # type: ignore[arg-type]
        source_repo="https://github.com/example/fixture",
        source_commit="fixture",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name=server,
        tool={"name": "t", "description": text, "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,
        labeller="t",
        labelled_at="2026-09-11T00:00:00Z",  # type: ignore[arg-type]
        split="dev",
        fold=0,
        mutation_of=host,
        payload_fields=["description"] if host else [],
    )


def corpus(tells: dict[str, str] | None = None) -> list[CorpusRecord]:
    """Real rows and one synthetic mutation per real row; `tells[label]` marks synthetic text."""
    tells = tells or {}
    rng = np.random.default_rng(0)
    out = []
    for s in range(20):
        for label in ("benign", "imperative_benign"):
            for k in range(2):
                body = " ".join(rng.choice(WORDS, size=8)) + f" {s}{k}."
                host = f"r-{label}-{s}-{k}"
                out.append(_record(host, label, body, "public_repo", f"s{s}", None))  # type: ignore[arg-type]
                for syn_label in (label, "poisoned"):
                    tell = tells.get(syn_label, "")
                    fresh = " ".join(rng.choice(WORDS, size=8)) + f" {s}{k}."
                    text = f"{tell} {fresh}".strip()
                    out.append(
                        _record(f"y-{syn_label}-{host}", syn_label, text, SYN, f"s{s}", host)
                    )  # type: ignore[arg-type]
    return out


def lengths(records: Sequence[CorpusRecord]) -> Any:
    return np.array([len(r.tool.description or "") for r in records])


def _always(_: CorpusRecord) -> bool:
    return True


def _run(
    tmp_path: Path,
    corpora: list[list[CorpusRecord]],
    *,
    check_only: bool = False,
    regenerate: Any = None,
) -> tuple[int, dict[str, Any], list[Any]]:
    calls: list[Any] = []
    state = {"i": 0}

    def load() -> list[CorpusRecord]:
        return corpora[min(state["i"], len(corpora) - 1)]

    def regen(strata: list[str], banned: list[str]) -> None:
        calls.append((strata, banned))
        state["i"] += 1

    out = tmp_path / "gate.json"
    code = gate.run_gate(
        load,
        regenerate or regen,
        out=out,
        check_only=check_only,
        lengths_fn=lengths,
        survives=_always,
        baseline_pairs=10,
    )
    return code, json.loads(out.read_text(encoding="utf-8")), calls


def test_a_clean_corpus_passes_without_tightening(tmp_path: Path) -> None:
    code, report, calls = _run(tmp_path, [corpus()])
    assert code == 0 and report["passed"] is True
    assert calls == [] and report["tightening_used"] is False
    checks = report["attempts"][0]
    assert checks["survival_failures"] == []
    assert checks["minimal_pairs"]["count"] >= 10


def test_a_failing_stratum_is_tightened_once_and_then_passes(tmp_path: Path) -> None:
    code, report, calls = _run(
        tmp_path, [corpus({"imperative_benign": "notwithstanding"}), corpus()]
    )
    assert code == 0 and report["passed"] is True
    ((strata, banned),) = calls
    assert "imperative_benign" in strata
    assert "notwithstanding" in banned and len(banned) <= 20 * len(strata)
    assert report["tightening_used"] is True and len(report["attempts"]) == 2


def test_failing_twice_is_a_stop(tmp_path: Path) -> None:
    bad = corpus({"imperative_benign": "notwithstanding"})
    code, report, calls = _run(tmp_path, [bad, bad])
    assert code == gate.STOP and report["passed"] is False
    assert len(calls) == 1 and report["stop"] is True


def test_check_only_never_regenerates(tmp_path: Path) -> None:
    code, report, calls = _run(tmp_path, [corpus({"benign": "notwithstanding"})], check_only=True)
    assert code == gate.STOP and calls == []
    code, _, _ = _run(tmp_path, [corpus()], check_only=True)
    assert code == 0


def test_the_one_tightening_pass_is_never_spent_twice(tmp_path: Path) -> None:
    bad = corpus({"imperative_benign": "notwithstanding"})
    _run(tmp_path, [bad, corpus()])  # spends the pass
    code, report, calls = _run(tmp_path, [bad])
    assert code == gate.STOP and calls == []
    assert report["tightening_used"] is True


def test_survival_failures_and_lost_pairs_fail_the_gate(tmp_path: Path) -> None:
    out = tmp_path / "g.json"
    code = gate.run_gate(
        lambda: corpus(), lambda s, b: None, out=out, check_only=True, lengths_fn=lengths,
        survives=lambda r: r.id != "y-poisoned-r-benign-0-0", baseline_pairs=10_000,
    )  # fmt: skip
    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == gate.STOP
    assert report["attempts"][0]["survival_failures"] == ["y-poisoned-r-benign-0-0"]
    assert report["attempts"][0]["minimal_pairs"]["passed"] is False


def test_the_top_ngrams_are_the_synthetic_side_of_the_stratum() -> None:
    grams = gate.top_ngrams(corpus({"poisoned": "kindly ensure"}), "poisoned", k=20)
    assert 0 < len(grams) <= 20
    assert {"kindly", "ensure"} & set(grams[:5])


def test_a_ban_never_targets_stop_words_or_the_real_register() -> None:
    """The real run's bug: "the", "and" and "tool" were banned, and the lint then rejected
    almost every payload. Only n-grams rare in the stratum's real rows may be banned."""
    records = []
    for r in corpus({"imperative_benign": "kindly always ensure the tool"}):
        if r.source != SYN and r.label == "imperative_benign":  # the real register has it too
            tool = r.tool.model_copy(update={"description": f"{r.tool.description} the tool"})
            r = r.model_copy(update={"tool": tool})
        records.append(r)
    grams = gate.top_ngrams(records, "imperative_benign", k=20)
    assert not {"the", "the tool", "tool"} & set(grams)
    assert {"kindly", "always ensure"} <= set(grams)


def test_with_payloads_a_ban_must_occur_in_a_generated_payload() -> None:
    """A host-side tell (which servers were mutated) is not a payload's to fix."""
    records = corpus({"imperative_benign": "kindly always ensure"})
    payloads = {r.id: "always ensure it" for r in records if r.source == SYN}
    grams = gate.top_ngrams(records, "imperative_benign", payloads=payloads)
    assert "always ensure" in grams and "kindly" not in grams


def test_each_failing_probe_names_the_stratum_it_regenerates() -> None:
    assert gate.STRATUM_OF == {
        "imperative": "imperative_benign", "benign": "benign", "surface": "imperative_benign",
        "length": "imperative_benign", "transfer": "poisoned",
    }  # fmt: skip
