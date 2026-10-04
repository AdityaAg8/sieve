"""The one-shot sealed evaluation. The only code in the repository that scores a sealed row.

It refuses to run without `SIEVE_FINAL=1`. Then, before anything is logged (training plan H2):

* **preflight**: `--model-dirs` is required; every model is loaded, a `fixture@`/`fake@` version
  or a non-finite threshold is refused, and each scores 8 dev rows; the output directory must be
  writable; the sealed inputs must exist; and `test_opened.log` must hold no earlier opening of
  this `dataset_version` (unless `--new-version`, which a reason must justify).

Only then is one line appended to the append-only log (`LOG_PATH`, fixed: tests patch the
constant), the sealed sets are read and scored by every model, and two files are written
atomically: `holdout_predictions.jsonl` (set, id, model, seed, p, probs, verdict) and
`holdout_rows.jsonl` (each sealed row's label, cluster and slice keys, never its text).
`bench/report_final.py` turns them into `holdout.json`; `make bench` re-runs only that.

The sealed sets, each reported only under its own name (`bench.split.split.sealed_views`):
`test_id` (the real holdout rows), `holdout_synthetic` (the release gate), `real_poisoned`
(M13, the MCPTox slice), and the test-OOD negatives `docker` and `mcptox_clean`, minus the
ids G4 dropped (`bench/results/dedup_cross.json`). Verdicts are Stage-1 only: the judge is
deferred to v1.1 (D8), so the band is `needs_review` and its share is the routing rate.

After it runs, no code, prompt, serialisation or synthesis change may follow for that version.
A change means a new version and a new log line (PRD §11.6).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from bench.ablations.run import dataset_version, pipeline_verdicts
from bench.collect.ood_rows import OodRow, load_rows
from bench.stats import EVAL_ONLY_SOURCE
from sieve.protocols import Classifier
from sieve.serialise import serialise
from sieve.types import LABELS, CorpusRecord, ToolRecord

log = logging.getLogger("bench.eval_final")

SEALED_MESSAGE = "the holdout is opened once per published version; see PRD §11.6"
LOG_PATH = Path("bench/results/test_opened.log")
RESULTS_DIR = Path("bench/results")
PREDICTIONS_FILE = "holdout_predictions.jsonl"
ROWS_FILE = "holdout_rows.jsonl"
REAL_SOURCES = frozenset({"public_repo", "wild_fp"})
SEALED_SPLITS = frozenset({"holdout", "eval_only"})
DEFAULT_CORPUS = Path("bench/data/labelled.jsonl")
REFUSED_VERSIONS = ("fixture@", "fake@")
SMOKE_ROWS = 8
SETS = ("test_id", "holdout_synthetic", "real_poisoned", "docker", "mcptox_clean")
_DIGITS = 10

Loader = Callable[[str], Classifier]
YaraRunner = Callable[[Sequence[ToolRecord]], list[bool]]


def m13_rows[R](rows: Sequence[R]) -> list[R]:
    """The M13 slice: poisoned rows whose source is `real_poisoned`, and nothing else. A
    test-OOD negative (G2) or any other row that lands in the eval-only split stays out."""
    return [
        r
        for r in rows
        if getattr(r, "source", None) == EVAL_ONLY_SOURCE
        and getattr(r, "label", None) == "poisoned"
    ]


class HoldoutSealed(Exception):  # noqa: N818 - named by the lane brief; it is a refusal
    """`bench/eval_final.py` was called without `SIEVE_FINAL=1`."""


class PreflightError(Exception):
    """A check that runs before the log line failed: nothing was opened."""


def require_unsealed() -> None:
    if os.environ.get("SIEVE_FINAL") != "1":
        raise HoldoutSealed(SEALED_MESSAGE)


# ------------------------------------------------------------------------------ inputs


@dataclass(frozen=True)
class SealedInputs:
    corpus: Path = DEFAULT_CORPUS
    real_poisoned: Path = Path("bench/data/real_poisoned.jsonl")
    ood: Path = Path("bench/data/ood/test_ood_negatives.jsonl")
    ood_yara: Path = Path("bench/data/ood/test_ood_negatives.yara.json")
    dedup: Path = Path("bench/results/dedup_cross.json")

    def missing(self) -> list[str]:
        return [str(p) for p in (self.corpus, self.real_poisoned, self.ood, self.ood_yara,
                                 self.dedup) if not p.exists()]  # fmt: skip


@dataclass(frozen=True)
class SealedRow:
    set: str
    id: str
    cluster: str
    poisoned: bool
    label: str | None
    source: str
    tool_record: ToolRecord
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedModel:
    model_dir: Path
    classifier: Classifier
    version: str
    seed: int | None
    family: str | None
    fold: int | None
    mode: str


def _load_corpus(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _dropped(inputs: SealedInputs) -> set[str]:
    report = json.loads(inputs.dedup.read_text(encoding="utf-8"))
    return {i for entry in report["sets"].values() for i in entry.get("dropped", [])}


def _record_meta(r: CorpusRecord) -> dict[str, Any]:
    from bench.collect.real_poisoned import mcptox_risk, mcptox_template

    return {
        "concealment": r.concealment,
        "difficulty": r.difficulty,
        "payload_fields": list(r.payload_fields),
        "schema_fidelity": r.schema_fidelity,
        "generator": r.generator_model,
        "template": mcptox_template(r),
        "risk": mcptox_risk(r),
    }


def _sealed_record(name: str, r: CorpusRecord, yara: bool | None) -> SealedRow:
    meta = _record_meta(r)
    cluster = r.server_name
    if name == "real_poisoned" and meta["template"]:
        cluster = f"{meta['template']}|{r.server_name}"  # template x server (§6.6)
    return SealedRow(
        set=name,
        id=r.id,
        cluster=cluster,
        poisoned=r.label == "poisoned",
        label=r.label,
        source=r.source,
        tool_record=r.to_tool_record(),
        meta={**meta, "yara": yara},
    )


def _sealed_ood(row: OodRow, yara: Mapping[str, bool]) -> SealedRow:
    return SealedRow(
        set=row.source,
        id=row.id,
        cluster=row.server_name,
        poisoned=row.poisoned,
        label=None,
        source=row.source,
        tool_record=row.tool_record(),
        meta={"yara": yara.get(row.id)},
    )


def _scan(records: Sequence[ToolRecord]) -> list[bool]:
    """Cisco YARA over tools renamed `t<i>`, static mode, nothing executed (F58)."""
    from bench.collect.ood_negatives import yara_sidecar

    rows = [
        OodRow(f"t{i}", "scan", r.server_name, r.tool.model_dump(exclude_none=True), False)
        for i, r in enumerate(records)
    ]
    # The scan input holds sealed text: it lives only as long as the scan (Z2 review).
    with tempfile.TemporaryDirectory(prefix="sieve-yara-") as work:
        flags = yara_sidecar(rows, Path(work))
    return [flags[f"t{i}"] for i in range(len(records))]


def scanner_smoke(yara: YaraRunner, corpus: Path) -> None:
    """Run the YARA scanner on one dev row before the log line. A missing or broken
    `mcp-scanner` used to fail inside `load_sealed`, after the opening was logged (Z2 review)."""
    dev = sorted((r for r in _load_corpus(corpus) if r.split == "dev"), key=lambda r: r.id)
    if not dev:
        raise PreflightError(f"{corpus} has no dev row to smoke-test the scanner on")
    try:
        flags = yara([dev[0].to_tool_record()])
    except Exception as error:  # noqa: BLE001 - any failure here must stop the opening
        raise PreflightError(f"the YARA scanner failed its smoke test: {error}") from error
    if len(flags) != 1 or not isinstance(flags[0], bool):
        raise PreflightError(f"the YARA scanner returned {flags!r} for one dev row")


@dataclass(frozen=True)
class _Row:
    """Just the fields `m13_rows` and the count read, from a raw JSONL line."""

    id: str
    source: str | None
    label: str | None

    @classmethod
    def of(cls, raw: Sequence[Mapping[str, Any]]) -> list[_Row]:
        return [cls(str(r["id"]), r.get("source"), r.get("label")) for r in raw]


def count_sealed(inputs: SealedInputs) -> int:
    """How many sealed rows the run is about to score, counted before any is read."""
    dropped = _dropped(inputs)
    corpus = sum(1 for r in _load_corpus(inputs.corpus) if r.split == "holdout")

    def rows(path: Path) -> list[dict[str, Any]]:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    # Only the M13 slice of real_poisoned is scored (Z2 review: every line used to count).
    poisoned = [r for r in m13_rows(_Row.of(rows(inputs.real_poisoned))) if r.id not in dropped]
    ood = [r for r in rows(inputs.ood) if r["id"] not in dropped]
    return corpus + len(poisoned) + len(ood)


def load_sealed(inputs: SealedInputs, *, yara: YaraRunner = _scan) -> list[SealedRow]:
    """Every sealed row, in `SETS` order then id order. Read only after the log line."""
    from bench.split.split import check_views, sealed_views

    corpus = _load_corpus(inputs.corpus)
    check_views(corpus)
    views = sealed_views(corpus)
    dropped = _dropped(inputs)
    poisoned = [r for r in _load_corpus(inputs.real_poisoned) if r.id not in dropped]
    synthetic = sorted(views["holdout_synthetic"], key=lambda r: r.id)
    scanned = yara([r.to_tool_record() for r in [*synthetic, *poisoned]])
    flag = dict(zip([r.id for r in [*synthetic, *poisoned]], scanned, strict=True))
    out = [
        _sealed_record("test_id", r, r.source == "wild_fp")
        for r in sorted(views["test_id"], key=lambda r: r.id)
    ]
    out += [_sealed_record("holdout_synthetic", r, flag[r.id]) for r in synthetic]
    out += [
        _sealed_record("real_poisoned", r, flag[r.id])
        for r in sorted(m13_rows(poisoned), key=lambda r: r.id)
    ]
    sidecar = json.loads(inputs.ood_yara.read_text(encoding="utf-8"))
    ood = [r for r in load_rows(inputs.ood) if r.id not in dropped]
    for name in ("docker", "mcptox_clean"):
        out += [
            _sealed_ood(r, sidecar) for r in sorted(ood, key=lambda r: r.id) if r.source == name
        ]
    return out


# --------------------------------------------------------------------------- preflight


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and math.isfinite(float(value))


def _config(model_dir: Path) -> dict[str, Any]:
    path = model_dir / "sieve_config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _default_loader(model_dir: str) -> Classifier:
    from sieve.model.infer import load_classifier

    return load_classifier(model_dir)


def load_models(model_dirs: Sequence[Path], loader: Loader = _default_loader) -> list[LoadedModel]:
    if not model_dirs:
        raise PreflightError("--model-dirs is required: name every artefact to score")
    models: list[LoadedModel] = []
    for model_dir in model_dirs:
        try:
            classifier = loader(str(model_dir))
        except Exception as exc:  # noqa: BLE001 - any load failure refuses the run
            raise PreflightError(f"{model_dir} does not load: {exc}") from exc
        version = str(classifier.model_version)
        if version.startswith(REFUSED_VERSIONS):
            raise PreflightError(f"{model_dir}: refusing a {version!r} model on sealed rows")
        t = classifier.thresholds
        for name in ("t_low", "t_high", "temperature"):
            if not _finite(getattr(t, name, None)):
                raise PreflightError(f"{model_dir}: threshold {name} is not finite")
        config = _config(Path(model_dir))
        models.append(
            LoadedModel(
                model_dir=Path(model_dir),
                classifier=classifier,
                version=version,
                seed=config.get("seed"),
                family=config.get("family"),
                fold=config.get("fold"),
                mode=str(config.get("mode") or "full"),
            )
        )
    return models


def smoke(models: Sequence[LoadedModel], corpus: Sequence[CorpusRecord]) -> None:
    dev = sorted((r for r in corpus if r.split == "dev"), key=lambda r: r.id)[:SMOKE_ROWS]
    if len(dev) < SMOKE_ROWS:
        raise PreflightError(f"only {len(dev)} dev rows to smoke-test on, need {SMOKE_ROWS}")
    for m in models:
        texts = [serialise(r.to_tool_record(), mode=m.mode) for r in dev]  # type: ignore[arg-type]
        probs = np.asarray(m.classifier.predict_proba(texts), dtype=np.float64)
        if probs.shape != (SMOKE_ROWS, len(LABELS)) or not np.isfinite(probs).all():
            raise PreflightError(f"{m.model_dir}: dev smoke returned {probs.shape} / non-finite")


def _writable(results_dir: Path) -> None:
    try:
        results_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=results_dir, delete=True):
            pass
    except OSError as exc:
        raise PreflightError(f"{results_dir} is not writable: {exc}") from exc


def earlier_openings(version: str, log_path: Path | None = None) -> list[dict[str, Any]]:
    path = log_path or LOG_PATH
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry.get("dataset_version") == version:
            entries.append(entry)
    return entries


def preflight_models(
    model_dirs: Sequence[Path], corpus: Path, results_dir: Path, *, loader: Loader = _default_loader
) -> list[LoadedModel]:
    """The model half of the preflight, which touches no sealed input: load every model, refuse
    fixture/fake versions and non-finite thresholds, smoke-score 8 dev rows, check the output
    directory. `bench.eval_val_ood --dry-run-on-dev` runs exactly this."""
    models = load_models(model_dirs, loader)
    smoke(models, _load_corpus(corpus))
    _writable(results_dir)
    return models


def preflight(
    model_dirs: Sequence[Path],
    inputs: SealedInputs,
    results_dir: Path,
    *,
    new_version: bool = False,
    reason: str = "",
    loader: Loader = _default_loader,
    yara: YaraRunner = _scan,
) -> tuple[list[LoadedModel], str]:
    """Every check that can fail without opening anything. Returns (models, dataset_version)."""
    if not reason.strip():
        raise PreflightError("a reason is required for every opening")
    missing = inputs.missing()
    if missing:
        raise PreflightError(f"sealed inputs missing: {missing}")
    models = preflight_models(model_dirs, inputs.corpus, results_dir, loader=loader)
    scanner_smoke(yara, inputs.corpus)
    _writable(LOG_PATH.parent)
    version = dataset_version(inputs.corpus)
    if earlier_openings(version) and not new_version:
        raise PreflightError(
            f"{version} was already opened (see {LOG_PATH}); a re-opening needs --new-version"
        )
    return models, version


# ------------------------------------------------------------------------- the opening


def append_log_line(
    *, rows_evaluated: int, reason: str, version: str, models: Sequence[str], new_version: bool
) -> str:
    """Append exactly one line. The file is opened with `"a"` and never truncated."""
    from sieve.model.version import git_sha

    entry = {
        "git_sha": git_sha(short=False),
        "utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "rows_evaluated": rows_evaluated,
        "reason": reason,
        "dataset_version": version,
        "models": list(models),
        "new_version": new_version,
    }
    line = json.dumps(entry, sort_keys=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")
    return line


def unk_flags(rows: Sequence[SealedRow], models: Sequence[LoadedModel]) -> dict[str, bool] | None:
    """Rows whose DeBERTa tokenisation holds `[UNK]` (a §6.6 slice), when a DeBERTa is scored."""
    deberta = next((m for m in models if "deberta" in (m.family or m.version).lower()), None)
    if deberta is None:
        return None
    from transformers import AutoTokenizer

    try:
        tokenizer: Any = AutoTokenizer.from_pretrained(str(deberta.model_dir))  # type: ignore[no-untyped-call]
    except (OSError, TypeError, ValueError) as exc:
        log.warning("no tokenizer in %s, so no [UNK] slice: %s", deberta.model_dir, exc)
        return None
    unk = tokenizer.unk_token_id
    return {
        r.id: unk
        in tokenizer(serialise(r.tool_record), truncation=True, max_length=512)["input_ids"]
        for r in rows
    }


def score(models: Sequence[LoadedModel], rows: Sequence[SealedRow]) -> list[dict[str, Any]]:
    """One prediction per (model, row): p(poisoned), the three probabilities and the verdict."""
    out: list[dict[str, Any]] = []
    for m in models:
        texts = [serialise(r.tool_record, mode=m.mode) for r in rows]  # type: ignore[arg-type]
        probs = np.asarray(m.classifier.predict_proba(texts), dtype=np.float64)
        p = probs[:, LABELS.index("poisoned")]
        # With no judge (deferred, D8) `pipeline_verdicts` reads only the thresholds.
        verdicts = pipeline_verdicts(rows, [float(x) for x in p], m.classifier.thresholds, None)  # type: ignore[arg-type]
        for r, row_probs, verdict in zip(rows, probs, verdicts, strict=True):
            out.append(
                {
                    "set": r.set,
                    "id": r.id,
                    "model": m.version,
                    "family": m.family,
                    "seed": m.seed,
                    "fold": m.fold,
                    "p": round(float(row_probs[LABELS.index("poisoned")]), _DIGITS),
                    "probs": [round(float(x), _DIGITS) for x in row_probs],
                    "verdict": verdict,
                    "t_low": m.classifier.thresholds.t_low,
                    "t_high": m.classifier.thresholds.t_high,
                }
            )
    return sorted(out, key=lambda d: (d["model"], SETS.index(d["set"]), d["id"]))


def row_entries(rows: Sequence[SealedRow], unk: Mapping[str, bool] | None) -> list[dict[str, Any]]:
    return [
        {
            "set": r.set,
            "id": r.id,
            "cluster": r.cluster,
            "server": r.tool_record.server_name,
            "poisoned": r.poisoned,
            "label": r.label,
            "source": r.source,
            **r.meta,
            "deberta_unk": None if unk is None else unk[r.id],
        }
        for r in rows
    ]


def write_jsonl_atomic(path: Path, entries: Iterable[Mapping[str, Any]]) -> Path:
    """Write to a sibling temp file, then `os.replace`: a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in entries)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def run(
    model_dirs: Sequence[Path],
    *,
    reason: str,
    inputs: SealedInputs | None = None,
    results_dir: Path = RESULTS_DIR,
    new_version: bool = False,
    loader: Loader = _default_loader,
    yara: YaraRunner = _scan,
) -> tuple[Path, Path]:
    """Open the sealed sets once. Returns (predictions path, rows path)."""
    require_unsealed()
    inputs = inputs or SealedInputs()
    models, version = preflight(
        model_dirs,
        inputs,
        results_dir,
        new_version=new_version,
        reason=reason,
        loader=loader,
        yara=yara,
    )
    # Logged before a sealed row is read, so a run that crashes half way through still leaves
    # a record that the seal was broken.
    append_log_line(
        rows_evaluated=count_sealed(inputs),
        reason=reason,
        version=version,
        models=[m.version for m in models],
        new_version=new_version,
    )
    rows = load_sealed(inputs, yara=yara)
    predictions = write_jsonl_atomic(results_dir / PREDICTIONS_FILE, score(models, rows))
    described = write_jsonl_atomic(
        results_dir / ROWS_FILE, row_entries(rows, unk_flags(rows, models))
    )
    log.info("scored %d sealed rows with %d model(s)", len(rows), len(models))
    return predictions, described


def cli(argv: Sequence[str] | None = None) -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(prog="python -m bench.eval_final")
    parser.add_argument("--reason", required=True, help='free text, e.g. "v1 release"')
    parser.add_argument("--model-dirs", type=Path, nargs="+", required=True)
    parser.add_argument("--new-version", action="store_true", help="re-open a new version")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--results-dir", type=Path, default=RESULTS_DIR)
    ns = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    return run(
        ns.model_dirs,
        reason=ns.reason,
        inputs=SealedInputs(corpus=ns.corpus),
        results_dir=ns.results_dir,
        new_version=ns.new_version,
    )


__all__ = [
    "LOG_PATH",
    "SEALED_MESSAGE",
    "HoldoutSealed",
    "PreflightError",
    "SealedInputs",
    "append_log_line",
    "cli",
    "load_sealed",
    "m13_rows",
    "preflight",
    "run",
    "score",
]

if __name__ == "__main__":  # pragma: no cover
    cli()
