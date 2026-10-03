"""Blind labelling that is actually blind (training plan F4: findings F39-F42)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bench.label import holdout_gold, kappa, sample, tui
from bench.label.session import LabelSession
from sieve.types import CorpusRecord


def _write(path: Path, records: list[CorpusRecord]) -> Path:
    path.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    return path


def _reload(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(x)
        for x in path.read_text(encoding="utf-8").splitlines()
        if x
    ]


def _synthetic(records: list[CorpusRecord]) -> CorpusRecord:
    return next(r for r in records if r.source == "synthetic_mutation" and r.sub_labels)


# ------------------------------------------------------------------------ F39: the pane


def test_blind_pane_hides_the_id_and_every_provenance_field(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records).model_copy(update={"id": "sb-syn-0042"})
    pane = LabelSession(_write(tmp_path / "c.jsonl", [row]), labeller="aditya", blind=True).render()
    for leak in ("sb-syn-0042", "synthetic_mutation", "source:", "capture", "fidelity", "authored"):
        assert leak not in pane, leak
    assert row.tool.name in pane


def test_blind_rows_come_in_a_seeded_permutation(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    corpus = _write(tmp_path / "c.jsonl", mini_records[:20])

    def walk(seed: int) -> list[str]:
        session = LabelSession(corpus, labeller="aditya", blind=True, order_seed=seed)
        seen = [session.current().id]
        for _ in range(19):
            seen.append(session.next().id)
        return seen

    file_order = [r.id for r in mini_records[:20]]
    assert walk(1) == walk(1)
    assert walk(1) != file_order and sorted(walk(1)) == sorted(file_order)
    assert walk(1) != walk(2)


def test_the_permutation_is_never_written_to_the_corpus(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    corpus = _write(tmp_path / "c.jsonl", mini_records[:20])
    session = LabelSession(corpus, labeller="aditya", blind=True, order_seed=1)
    session.set_label("benign")
    session.save()
    assert [r.id for r in _reload(corpus)] == [r.id for r in mini_records[:20]]


def test_sighted_mode_keeps_file_order(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    session = LabelSession(_write(tmp_path / "c.jsonl", mini_records[:5]), labeller="aditya")
    assert [session.current().id] + [session.next().id for _ in range(4)] == [
        r.id for r in mini_records[:5]
    ]


def test_a_session_can_present_only_chosen_ids(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    corpus = _write(tmp_path / "c.jsonl", mini_records[:10])
    chosen = [mini_records[3].id, mini_records[7].id]
    session = LabelSession(corpus, labeller="aditya", blind=True, ids=chosen)
    assert session.progress() == (1, 2)
    assert {session.current().id, session.next().id} == set(chosen)


def test_unknown_ids_are_refused(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    with pytest.raises(ValueError, match="nope"):
        LabelSession(
            _write(tmp_path / "c.jsonl", mini_records[:3]), labeller="aditya", ids=["nope"]
        )


# --------------------------------------------------------------------- F40: the sidecar


def test_the_intent_sidecar_is_first_wins_across_a_resume(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    corpus = _write(tmp_path / "c.jsonl", [row])
    intent = tmp_path / "intent.jsonl"
    first = LabelSession(corpus, labeller="aditya", blind=True, intent_path=intent)
    first.set_label("benign")
    first.save()
    resumed = LabelSession(corpus, labeller="retest", blind=True, intent_path=intent)
    resumed.set_label("imperative_benign")
    resumed.set_rationale("second look")
    resumed.save()
    lines = [json.loads(x) for x in intent.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["intended_label"] == row.label


def test_kappa_reads_the_first_intent_entry_for_an_id(tmp_path: Path) -> None:
    path = tmp_path / "intent.jsonl"
    path.write_text(
        json.dumps({"id": "a", "intended_label": "poisoned"})
        + "\n"
        + json.dumps({"id": "a", "intended_label": "benign"})
        + "\n",
        encoding="utf-8",
    )
    assert kappa._load_intent(path) == {"a": "poisoned"}


# ---------------------------------------------------------- F41: the generator's fields


def test_first_blind_touch_clears_the_generators_sub_labels_tier_and_rationale(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records).model_copy(
        update={"difficulty": "hard", "label_rationale": "generator said so"}
    )
    corpus = _write(tmp_path / "c.jsonl", [row])
    session = LabelSession(corpus, labeller="aditya", blind=True, intent_path=tmp_path / "i.jsonl")
    first_sub = row.sub_labels[0]
    session.toggle_sub_label(first_sub)
    record = session.current()
    assert record.sub_labels == [
        first_sub
    ]  # the human's own pick, not a toggle-off of the hidden one
    assert record.difficulty is None and record.label_rationale is None


def test_the_blind_pane_shows_the_humans_own_selections(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    session = LabelSession(
        _write(tmp_path / "c.jsonl", [row]),
        labeller="aditya",
        blind=True,
        intent_path=tmp_path / "i.jsonl",
    )
    session.set_label("poisoned")
    session.toggle_sub_label("exfiltration")
    pane = session.render()
    assert "your label = poisoned" in pane and "exfiltration" in pane


def test_a_resumed_row_already_labelled_by_the_human_is_not_cleared(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    corpus = _write(tmp_path / "c.jsonl", [row])
    first = LabelSession(corpus, labeller="aditya", blind=True, intent_path=tmp_path / "i.jsonl")
    first.set_label("poisoned")
    first.toggle_sub_label("exfiltration")
    first.set_rationale("reads secrets")
    first.save()
    again = LabelSession(corpus, labeller="aditya", blind=True, intent_path=tmp_path / "i.jsonl")
    again.set_difficulty("easy")
    assert again.current().sub_labels == ["exfiltration"]
    assert again.current().label_rationale == "reads secrets"


def test_second_mode_never_edits_the_primary_fields(
    tmp_path: Path, mini_records: list[CorpusRecord]
) -> None:
    row = _synthetic(mini_records)
    corpus = _write(tmp_path / "c.jsonl", [row])
    session = LabelSession(
        corpus, labeller="second", blind=True, second=True, intent_path=tmp_path / "i.jsonl"
    )
    session.toggle_sub_label("command_execution")
    session.set_difficulty("easy")
    session.set_rationale("second read")
    session.set_label("benign")
    session.save()
    out = _reload(corpus)[0]
    assert (out.sub_labels, out.difficulty, out.label_rationale) == (
        row.sub_labels,
        row.difficulty,
        row.label_rationale,
    )


# ------------------------------------------------------------------------- F42: the CLI


def test_second_implies_blind(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    corpus = _write(tmp_path / "c.jsonl", mini_records[:3])
    session = tui.build_session(tui.parse_args([str(corpus), "--second", "--labeller", "b"]))
    assert session.blind and session.second


# -------------------------------------------------------------------- the two sessions


def test_dev_audit_ids_are_50_seeded_real_dev_rows(mini_records: list[CorpusRecord]) -> None:
    rows = [
        r.model_copy(
            update={
                "id": f"r{i}",
                "source": "public_repo" if i % 3 else "synthetic_mutation",
                "split": "dev" if i % 5 else "holdout",
                "fold": 0 if i % 5 else None,
            }
        )
        for i, r in enumerate(mini_records * 4)
    ]
    ids = sample.dev_audit_ids(rows)
    assert len(ids) == 50 and len(set(ids)) == 50
    assert ids == sample.dev_audit_ids(rows)
    chosen = {r.id: r for r in rows if r.id in set(ids)}
    assert all(r.split == "dev" and r.source in ("public_repo", "wild_fp") for r in chosen.values())
    assert sample.DEV_AUDIT_SEED == 20261002


def test_holdout_gold_round_trip(tmp_path: Path, mini_records: list[CorpusRecord]) -> None:
    sealed = [
        r.model_copy(
            update={"id": f"h{i}", "split": "holdout", "fold": None, "source": "public_repo"}
        )
        for i, r in enumerate(mini_records[:4])
    ]
    synthetic = mini_records[5].model_copy(
        update={"id": "hs", "split": "holdout", "fold": None, "source": "synthetic_mutation"}
    )
    labelled = _write(tmp_path / "labelled.jsonl", [*sealed, synthetic, mini_records[6]])
    work, gold = tmp_path / "work.jsonl", tmp_path / "gold.jsonl"
    assert holdout_gold.prepare(labelled, work) == 4
    session = LabelSession(work, labeller="aditya", blind=True, intent_path=tmp_path / "i.jsonl")
    session.set_label("benign")
    session.save()
    assert holdout_gold.export(work, gold) == 1
    got = holdout_gold.load_gold(gold)
    assert list(got) == [session.current().id] and got[session.current().id] == "benign"
    assert holdout_gold.prepare(labelled, work) == 4  # resuming keeps the work file
    assert len(holdout_gold.load_gold(gold)) == 1


def test_holdout_gold_imports_no_model_code() -> None:
    code = (
        "import sys, bench.label.holdout_gold; "
        "print([m for m in sys.modules if m.startswith(('sieve.model', 'torch', 'transformers'))])"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # noqa: S603
    assert out.stdout.strip() == "[]"


def test_the_make_targets_exist() -> None:
    for target in ("label-dev-audit", "label-test-id"):
        done = subprocess.run(["make", "-n", target], capture_output=True, text=True, check=False)  # noqa: S603, S607
        assert done.returncode == 0, done.stderr
