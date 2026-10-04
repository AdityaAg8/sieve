"""The ensemble vote (training plan F2) and the synthetic dispute rule (E5), §4.1 and §8 risk 7."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from bench.label import vote as vt
from bench.ollama import RawLog
from sieve.types import CorpusRecord, Label

Q, G = vt.MODELS["qwen"], vt.MODELS["gemma"]


def _row(
    rid: str,
    label: Label = "benign",
    *,
    labeller: str = "claude-sonnet-5-provisional",
    source: str = "public_repo",
    generator: str | None = None,
    split: str = "dev",
) -> CorpusRecord:
    return CorpusRecord(
        id=rid,
        source=source,  # type: ignore[arg-type]
        source_repo="https://example.invalid/r",
        source_commit="c",
        source_license="MIT",
        capture_method="authored",
        schema_fidelity="exact",
        server_name="s",
        tool={"name": "t", "description": f"text of {rid}", "inputSchema": {"type": "object"}},  # type: ignore[arg-type]
        label=label,
        labeller=labeller,
        labelled_at="2026-09-21T00:00:00Z",  # type: ignore[arg-type]
        generator_model=generator,
        split=split,  # type: ignore[arg-type]
    )


B, IMP, P = "benign", "imperative_benign", "poisoned"


@pytest.mark.parametrize(
    ("votes", "label", "queue"),
    [
        ((B, B, IMP), B, None),
        ((IMP, IMP, B), IMP, None),
        ((IMP, B, IMP), IMP, None),
        ((B, B, B), B, None),
        ((B, B, None), B, None),  # one parse failure: the other two agree
        ((None, IMP, IMP), IMP, None),  # the Sonnet label missing: the two local votes agree
        ((B, IMP, None), None, "parse_failure_split"),  # one failure, the remaining two split
        ((None, None, B), None, "too_few_votes"),
        ((P, B, B), None, "poisoned_vote"),  # ANY poisoned vote goes to a human
        ((B, B, P), None, "poisoned_vote"),
        ((None, P, None), None, "poisoned_vote"),
    ],
)
def test_the_real_row_rule(votes: tuple[Any, ...], label: str | None, queue: str | None) -> None:
    decision = vt.decide_real(dict(zip(("sonnet", Q, G), votes, strict=True)))
    assert (decision.label, decision.queue) == (label, queue)


def test_a_synthetic_row_is_disputed_when_its_checker_disagrees_with_construction() -> None:
    assert vt.disputed(P, {G: P}) is False
    assert vt.disputed(P, {G: B}) is True
    assert vt.disputed(IMP, {Q: B}) is True
    assert vt.disputed(P, {G: None}) is False  # a parse failure is not a vote against
    assert vt.disputed(P, {Q: P, G: IMP}) is True  # hand-written: both check, either disputes


# ---------------------------------------------------------------------------------- world


def _log(path: Path, votes: dict[tuple[str, str], Label | None]) -> RawLog:
    log = RawLog(path)
    for (model, rid), label in votes.items():
        status = "ok" if label else "parse_failure"
        log.put(
            model, rid, {"status": status, "label": label, "poisoned": label == P, "rationale": "r"}
        )
    return log


class World:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        rows = [
            _row("real-1"),  # sonnet B, local B/IMP -> B
            _row("real-2"),  # local IMP/IMP -> IMP
            _row("real-3", labeller="aditya", label=IMP),  # dev-50 human label: wins
            _row("real-4"),  # a poisoned vote -> queue
            _row("real-5", split="holdout"),  # never a training label
            _row("sb-syn-0000", P, labeller="generator", source="synthetic_mutation", generator=Q),
            _row("sb-syn-0001", P, labeller="generator", source="synthetic_mutation", generator=Q),
            _row(
                "sb-hum-0000",
                P,
                labeller="generator",
                source="synthetic_mutation",
                generator="human",
            ),
        ]
        self.paths = vt.Paths(
            corpus=root / "corpus.jsonl",
            provisional=root / "provisional.json",
            log=root / "ensemble.jsonl",
            recheck_log=root / "recheck.jsonl",
            payloads=root / "payloads_v1.jsonl",
            state=root / "disputes.json",
            queue=root / "review_queue_ids.json",
            agreement=root / "ensemble_agreement.json",
            majority=root / "majority.json",
        )
        self.write(rows)
        self.paths.provisional.write_text(json.dumps({"labels": {
            "real-1": {"label": B}, "real-2": {"label": B}, "real-3": {"label": B},
            "real-4": {"label": B}, "real-5": {"label": B}}}), encoding="utf-8")  # fmt: skip
        _log(self.paths.log, {
            (Q, "real-1"): B, (G, "real-1"): IMP, (Q, "real-2"): IMP, (G, "real-2"): IMP,
            (Q, "real-3"): B, (G, "real-3"): B, (Q, "real-4"): P, (G, "real-4"): B,
            (Q, "real-5"): IMP, (G, "real-5"): IMP,
            (G, "sb-syn-0000"): P, (G, "sb-syn-0001"): B,
            (Q, "sb-hum-0000"): P, (G, "sb-hum-0000"): B,
        })  # fmt: skip
        self.paths.payloads.write_text(
            "".join(json.dumps({"plan_index": i, "payload": f"p{i}", "generator_model": Q,
                                "dropped": None}) + "\n" for i in range(2)),
            encoding="utf-8", newline="\n",
        )  # fmt: skip
        self.regenerated: list[list[str]] = []
        self.rebuilt = 0
        self.recheck: dict[str, Label] = {"sb-syn-0001": B}  # still disputed after regeneration

    def write(self, rows: list[CorpusRecord]) -> None:
        self.paths.corpus.write_text(
            "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8", newline="\n"
        )

    def rows(self) -> dict[str, CorpusRecord]:
        return {r.id: r for r in vt.load(self.paths.corpus)}

    def regenerate(self, ids: list[str]) -> None:
        self.regenerated.append(ids)
        _log(self.paths.recheck_log, {(G, rid): self.recheck[rid] for rid in ids})

    def rebuild(self) -> None:
        """`make corpus`: a dropped payload's row leaves the build and is retired."""
        self.rebuilt += 1
        dropped = {f"sb-syn-{json.loads(x)['plan_index']:04d}"
                   for x in self.paths.payloads.read_text(encoding="utf-8").splitlines()
                   if json.loads(x)["payload"] is None}  # fmt: skip
        self.write([r for r in self.rows().values() if r.id not in dropped])

    def run(self) -> int:
        return vt.run_vote(self.paths, regenerate=self.regenerate, rebuild=self.rebuild)


def test_labels_are_voted_and_human_labels_win(tmp_path: Path) -> None:
    world = World(tmp_path)
    assert world.run() == 0
    rows = world.rows()
    assert (rows["real-1"].label, rows["real-1"].labeller) == (B, vt.LABELLER)
    assert rows["real-2"].label == IMP
    assert (rows["real-3"].label, rows["real-3"].labeller) == (IMP, "aditya")  # untouched
    assert rows["real-4"].labeller == "claude-sonnet-5-provisional"  # waits for the human
    assert rows["real-5"].labeller == "claude-sonnet-5-provisional"  # sealed: never relabelled
    assert json.loads(world.paths.queue.read_text(encoding="utf-8")) == ["real-4"]


def test_a_disputed_synthetic_row_is_regenerated_once_then_dropped(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.run()
    assert world.regenerated == [["sb-syn-0001"]]  # the hand-written row is never regenerated
    state = json.loads(world.paths.state.read_text(encoding="utf-8"))
    assert state["dropped"] == ["sb-syn-0001"] and state["human_disputed"] == ["sb-hum-0000"]
    payloads = [
        json.loads(x) for x in world.paths.payloads.read_text(encoding="utf-8").splitlines()
    ]
    assert payloads[1]["payload"] is None and "dispute" in payloads[1]["dropped"]
    assert payloads[0]["payload"] == "p0"
    assert "sb-syn-0001" not in world.rows()


def test_a_dispute_the_regeneration_resolves_keeps_the_row(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.recheck = {"sb-syn-0001": P}
    world.run()
    state = json.loads(world.paths.state.read_text(encoding="utf-8"))
    assert state["dropped"] == [] and state["regenerated"] == ["sb-syn-0001"]
    assert "sb-syn-0001" in world.rows()


def test_the_regeneration_round_is_never_spent_twice(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.run()
    world.run()
    assert len(world.regenerated) == 1


def test_a_regeneration_that_failed_half_way_is_retried_on_resume(tmp_path: Path) -> None:
    """The ids are frozen at round 1; the work is retried until it completes once."""
    world = World(tmp_path)
    calls: list[list[str]] = []

    def broken(ids: list[str]) -> None:
        calls.append(ids)
        raise RuntimeError("make corpus failed")

    with pytest.raises(RuntimeError):
        vt.run_vote(world.paths, regenerate=broken, rebuild=world.rebuild)
    assert world.run() == 0
    assert world.regenerated == [calls[0]], "retried once, with the same frozen ids"
    world.run()
    assert len(world.regenerated) == 1


def test_the_agreement_report_carries_kappas_rejection_rate_and_parse_failures(
    tmp_path: Path,
) -> None:
    world = World(tmp_path)
    world.run()
    report = json.loads(world.paths.agreement.read_text(encoding="utf-8"))
    assert set(report["pairwise_kappa"]) == {"sonnet~qwen", "sonnet~gemma", "qwen~gemma"}
    assert report["synthetic"]["rejection_rate"] == pytest.approx(1 / 2)  # 1 of 2 model rows
    assert report["synthetic"]["checker_agreement_round0"] == pytest.approx(2 / 4)  # per vote
    assert report["review_queue"] == 1
    assert report["parse_failures"] == {Q: 0, G: 0}


def test_a_parse_failure_is_counted(tmp_path: Path) -> None:
    world = World(tmp_path)
    _log(world.paths.log, {(G, "real-1"): None})
    world.run()
    report = json.loads(world.paths.agreement.read_text(encoding="utf-8"))
    assert report["parse_failures"][G] == 1
    assert world.rows()["real-1"].label == B  # sonnet B + qwen B still agree


def test_the_majority_file_covers_every_voted_row_for_the_audits(tmp_path: Path) -> None:
    world = World(tmp_path)
    world.run()
    majority = json.loads(world.paths.majority.read_text(encoding="utf-8"))
    assert majority["real-5"] == IMP and majority["real-1"] == B and majority["real-4"] == P


def test_splits_come_from_the_built_view_when_corpus_rows_carry_none(tmp_path: Path) -> None:
    """Z2: corpus.jsonl has split=None on every row; reading it skipped every real row."""
    world = World(tmp_path)
    original = world.rows()
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text(
        "".join(r.model_dump_json() + "\n" for r in original.values()),
        encoding="utf-8",
        newline="\n",
    )
    world.write([r.model_copy(update={"split": None}) for r in original.values()])
    world.paths = dataclasses.replace(world.paths, labelled=labelled)
    assert world.run() == 0
    rows = world.rows()
    assert (rows["real-1"].label, rows["real-1"].labeller) == (B, vt.LABELLER)
    assert rows["real-5"].labeller == "claude-sonnet-5-provisional"  # sealed in the view
    assert json.loads(world.paths.queue.read_text(encoding="utf-8")) == ["real-4"]
