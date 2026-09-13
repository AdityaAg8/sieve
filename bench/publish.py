"""Build the Hugging Face dataset folder from the corpus, and optionally upload it.

`--dry-run` writes `bench/data/hub/{README.md,dev.jsonl,holdout.jsonl,eval_only.jsonl,NOTICE}`
and uploads nothing; that is the form CI and review use. The real upload is a deliberate,
human-run step.

Three things are gates rather than warnings, because each one is a licensing or methodology
error that nobody would notice afterwards:

* an **MPL-2.0** record reaching publication (PRD SC-6: file-level copyleft, excluded),
* a record missing any of `source_repo` / `source_commit` / `source_license`,
* a record with no `split`, which would silently vanish from all three files.

The split files are named by the `split` field. There is no train/val/test any more: every
decision comes from pooled out-of-fold predictions over the `dev` servers, `holdout` is
opened once by `bench/eval_final.py`, and `eval_only` is the `real_poisoned` slice.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

from bench.collect.registry import PERMISSIVE_LICENCES
from sieve.types import CorpusRecord

DEFAULT_CORPUS = Path("bench/data/corpus.jsonl")
DEFAULT_STATS = Path("bench/results/corpus_stats.json")
DEFAULT_AGREEMENT = Path("bench/results/agreement.json")
DEFAULT_LEAKAGE = Path("bench/results/leakage_probe.json")
DEFAULT_OUT = Path("bench/data/hub")
DATASHEET = Path("bench/datasheet.md")

DEFAULT_POINTERS = Path("bench/data/real_poisoned.pointers.json")
SPLIT_FILES = ("dev", "holdout", "eval_only")

APACHE_ATTRIBUTION = """
The following records are distributed under the Apache License, Version 2.0. You may obtain
a copy of the License at http://www.apache.org/licenses/LICENSE-2.0. Files carrying that
licence are provided "AS IS", WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND. Where an
upstream repository ships its own NOTICE file, its attributions are reproduced beneath that
repository's entry below.
""".strip()

NOTICE_HEADER = """
SieveBench
==========

The annotations, taxonomy, splits and schema in this dataset are licensed CC BY 4.0.

The `tool`, `server_instructions` and `sibling_tool_names` fields of each record remain
under that record's own source licence, recorded per record as `source_license`. Every
source repository, the commit the text was taken from, and its licence are listed below.

To request removal of any record, contact the maintainer named in the dataset card; records
are removed within 7 days and the dataset version is re-tagged.
""".strip()


def load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def pointer_urls(pointers: Path) -> set[str]:
    """The citations of sources whose licence forbids redistributing their text."""
    if not pointers.exists():
        return set()
    entries = json.loads(pointers.read_text(encoding="utf-8"))
    return {str(e["url"]) for e in entries if isinstance(e, dict) and e.get("url")}


def check(records: list[CorpusRecord], *, blocked_urls: set[str] | None = None) -> list[str]:
    """Every reason this corpus must not be published. Empty means publishable.

    The licence test is an **allow-list**, not a deny-list. A deny-list of one entry passes
    GPL-3.0, AGPL and the literal "All rights reserved" that `reconstruct()` stamps on rows
    rebuilt from a non-redistributable pointer — and `write_folder` would upload them.

    Pointer-derived rows are refused on top of that, whatever their licence string says: the
    reason a pointer exists is that those bytes may not be redistributed, and a permissive
    string on such a row is a mistake, not a permission.
    """
    problems: list[str] = []
    blocked = blocked_urls or set()
    for record in records:
        if record.source_license not in PERMISSIVE_LICENCES:
            problems.append(
                f"{record.id}: {record.source_license!r} is not on the permissive allow-list "
                f"{sorted(PERMISSIVE_LICENCES)} (PRD SC-6)"
            )
        if record.source_repo in blocked:
            problems.append(
                f"{record.id}: derived from a non-redistributable pointer "
                f"({record.source_repo}); its text is never published"
            )
        missing = [
            field
            for field in ("source_repo", "source_commit", "source_license")
            if not (getattr(record, field) or "").strip()
        ]
        if missing:
            problems.append(f"{record.id}: missing provenance {', '.join(missing)}")
        if record.split is None:
            problems.append(f"{record.id}: has no split and would not be published at all")
    return problems


def build_notice(records: list[CorpusRecord]) -> str:
    """One entry per (repo, commit, licence), with the Apache-2.0 attribution when needed."""
    sources = sorted({(r.source_repo, r.source_commit, r.source_license) for r in records})
    lines = [NOTICE_HEADER, "", "Sources", "-------", ""]
    for repo, commit, licence in sources:
        count = sum(
            1
            for r in records
            if (r.source_repo, r.source_commit, r.source_license) == (repo, commit, licence)
        )
        lines.append(f"- {repo}")
        lines.append(f"  commit:  {commit}")
        lines.append(f"  licence: {licence}")
        lines.append(f"  records: {count}")
        lines.append("")
    if any(licence == "Apache-2.0" for _, _, licence in sources):
        lines += ["Apache-2.0 attribution", "----------------------", "", APACHE_ATTRIBUTION, ""]
    return "\n".join(lines)


def _composition(records: list[CorpusRecord], stats: dict[str, Any]) -> str:
    counts = collections.Counter(r.split for r in records)
    lines = [
        f"- Records: {stats.get('n', len(records))}",
        "- Splits: "
        + ", ".join(f"`{name}` {counts.get(name, 0)}" for name in SPLIT_FILES)
        + " (`dev` carries the 5 grouped folds; `holdout` and `eval_only` carry none)",
    ]
    for field, table in (stats.get("counts") or {}).items():
        rendered = ", ".join(f"`{k}` {v}" for k, v in table.items())
        lines.append(f"- {field.replace('_', ' ').capitalize()}: {rendered}")
    if "exact_share" in stats:
        lines.append(f"- Exact schema fidelity: {float(stats['exact_share']):.1%} (target ≥ 60%)")
    if "hand_authored_poisoned_share" in stats:
        lines.append(
            "- Hand-authored share of poisoned payloads: "
            f"{float(stats['hand_authored_poisoned_share']):.1%} (target ≥ 25%)"
        )
    return "\n".join(lines)


def _num(entry: dict[str, Any], key: str, fmt: str = ".3f") -> str:
    """A number from a partially-written results file, or `?`. A half-run must not crash."""
    value = entry.get(key)
    return format(float(value), fmt) if isinstance(value, int | float) else "?"


def _agreement(agreement: dict[str, Any]) -> str:
    if not agreement:
        return "_Agreement has not been computed yet (`python -m bench.label.kappa`)._"
    lines: list[str] = []
    for which in ("inter", "retest", "generator"):
        entry = agreement.get(which)
        if not entry:
            continue
        if which == "generator":
            lines.append(
                f"- Blind-vs-generator agreement: {_num(entry, 'blind_vs_generator')} "
                f"over n = {entry.get('n', '?')}. Not an inter-rater statistic."
            )
            continue
        name = "Inter-rater" if which == "inter" else "Test-retest"
        lines.append(
            f"- {name} κ: overall {_num(entry, 'kappa_overall')}, "
            f"real subset {_num(entry, 'kappa_real_subset')} "
            f"(n = {entry.get('n_real_subset', '?')}), sampler `{entry.get('sampler', '?')}`."
        )
        for label, per in (entry.get("per_class") or {}).items():
            lines.append(
                f"  - `{label}`: observed {_num(per, 'observed_agreement')}, "
                f"AC1 {_num(per, 'ac1')}, n = {per.get('n', '?')}."
            )
    return "\n".join(lines) or "_Agreement has not been computed yet._"


def _leakage(leakage: dict[str, Any], stats: dict[str, Any]) -> str:
    lines: list[str] = []
    if leakage:
        verdict = "passes" if leakage.get("passed") else "**fails**"
        lines.append(
            f"Pooled out-of-fold AUROC {_num(leakage, 'auroc')} over n = "
            f"{leakage.get('n', '?')} `imperative_benign` rows; the gate is ≤ "
            f"{_num(leakage, 'gate', '.2f')}, so this {verdict}."
        )
    else:
        lines.append("_The probe has not been run yet (`python -m bench.analysis.leakage_probe`)._")
    pairs = stats.get("cross_boundary_near_duplicate_pairs")
    if pairs is not None:
        lines.append("")
        lines.append(
            f"**Cross-boundary near-duplicate pairs: {pairs}.** Deduplication is deliberately "
            "stratum-local — it never compares a record with its mutation host, a sibling "
            "mutation, or a record of a different label — so near-duplicates can survive "
            "across the dev/holdout boundary. This is the count of such pairs at the same "
            "embedder and threshold dedup uses. It is a disclosure, not a gate: dropping "
            "them would break the mutation graph, and hiding them would overstate what the "
            "holdout measures."
        )
    return "\n".join(lines)


def build_card(
    records: list[CorpusRecord],
    *,
    stats: dict[str, Any],
    agreement: dict[str, Any],
    leakage: dict[str, Any],
    datasheet: Path = DATASHEET,
) -> str:
    """The dataset card is the datasheet with the generated numbers substituted in."""
    text = datasheet.read_text(encoding="utf-8")
    opened = stats.get("test_opened_log")
    return (
        text.replace("<!-- COMPOSITION -->", _composition(records, stats))
        .replace("<!-- AGREEMENT -->", _agreement(agreement))
        .replace("<!-- LEAKAGE -->", _leakage(leakage, stats))
        .replace(
            "<!-- TEST_OPENED -->",
            str(opened)
            if opened
            else "_The holdout has not been opened yet; `test_opened.log` is empty._",
        )
    )


def write_folder(
    records: list[CorpusRecord],
    out: Path,
    *,
    stats: dict[str, Any],
    agreement: dict[str, Any],
    leakage: dict[str, Any],
) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for name in SPLIT_FILES:
        rows = [r for r in records if r.split == name]
        (out / f"{name}.jsonl").write_text(
            "".join(r.model_dump_json() + "\n" for r in rows), encoding="utf-8", newline="\n"
        )
    (out / "README.md").write_text(
        build_card(records, stats=stats, agreement=agreement, leakage=leakage),
        encoding="utf-8",
        newline="\n",
    )
    (out / "NOTICE").write_text(build_notice(records), encoding="utf-8", newline="\n")
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.publish",
        description="Build the Hugging Face dataset folder; upload only when asked to.",
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--stats", type=Path, default=DEFAULT_STATS)
    parser.add_argument("--agreement", type=Path, default=DEFAULT_AGREEMENT)
    parser.add_argument("--leakage", type=Path, default=DEFAULT_LEAKAGE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--pointers",
        type=Path,
        default=DEFAULT_POINTERS,
        help="non-redistributable sources; any record citing one of these is refused",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="build the folder and upload nothing"
    )
    parser.add_argument("--repo-id", default=None, help="Hub dataset repo, e.g. user/sievebench")
    args = parser.parse_args(argv)
    if not args.dry_run and not args.repo_id:
        parser.error("--repo-id is required for a real upload; use --dry-run to build locally")

    records = load(args.corpus)
    problems = check(records, blocked_urls=pointer_urls(args.pointers))
    if problems:
        for problem in problems:
            print(f"REFUSING TO PUBLISH: {problem}")
        return 1

    folder = write_folder(
        records,
        args.out,
        stats=_read_json(args.stats),
        agreement=_read_json(args.agreement),
        leakage=_read_json(args.leakage),
    )
    print(f"built {folder} with {len(records)} records")
    if args.dry_run:
        return 0

    from huggingface_hub import HfApi

    HfApi().upload_folder(
        folder_path=str(folder),
        repo_id=args.repo_id,
        repo_type="dataset",
        commit_message="Publish SieveBench",
    )
    print(f"uploaded {folder} to {args.repo_id}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
