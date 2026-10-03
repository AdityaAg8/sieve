"""Per-fold encoder training. Reads `split == "dev"` rows and nothing else.

There is no train/val/test run here and no `test_logits.npy` in this design: a fold's held-out
servers produce `oof_logits.npy`, early stopping watches a slice of the fold's *training*
servers, and the holdout is reachable only from `bench/eval_final.py` (PRD §11.6).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import logging
import platform
import shutil
import time
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import yaml
from pydantic import BaseModel, ConfigDict, Field

from sieve.model.data import (
    ID2LABEL,
    LABEL2ID,
    add_markers,
    build_splits,
    load_corpus,
    load_scanner_findings,
)
from sieve.model.tokenizer_guard import TOKENIZER_NOTE
from sieve.model.version import dirty_paths, git_sha, require_clean_tree
from sieve.serialise import param_lines, serialise
from sieve.types import LABELS, CorpusRecord, EvidenceField, Label, ScannerFinding

log = logging.getLogger("sieve.model.train")

Precision = Literal["fp32", "bf16"]
Mode = Literal["full", "description_only"]
Fold = int | Literal["all"]

TWO_CLASS_ID2LABEL: dict[int, str] = {0: "not_poisoned", 1: "poisoned"}


class PrecisionRefused(ValueError):  # noqa: N818 - a refusal, not an error condition
    """bf16 was asked for on a DeBERTa base model without `--allow-reduced-precision`."""


class TrainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_model: str
    # Hub commit of `base_model` (training plan A5): a moving `main` would change the weights
    # under an unchanged config. None only for the CI smoke profile.
    revision: str | None = None
    # Speed/memory variant of a profile family (B10); `metrics.json` records it.
    variant: str | None = None
    max_length: int
    lr: float
    warmup_ratio: float = 0.06
    batch_size: int
    epochs: int
    patience: int
    precision: Precision
    seed: int
    fold: Fold
    mode: Mode = "full"
    num_labels: int = Field(default=3, ge=2, le=3)
    class_weights: bool
    gradient_checkpointing: bool = False
    scanner_features: bool = False
    # Record-id-keyed YARA findings (`baseline_raw.json`) that `--scanner-features` renders.
    findings: Path | None = None
    profile: str = "custom"
    allow_reduced_precision: bool = False
    allow_dirty: bool = False
    epochs_explicit: bool = False

    def __init__(self, **data: Any) -> None:
        # Guards live here, not in a `model_validator`: pydantic wraps every validator error in
        # a `ValidationError`, and `PrecisionRefused` has to reach the caller as itself so the
        # FP32 rule reads as a refusal rather than as a schema complaint (PRD §7.2).
        super().__init__(**data)
        self._guard()

    def _guard(self) -> None:
        if (
            self.precision == "bf16"
            and "deberta" in self.base_model.lower()
            and not self.allow_reduced_precision
        ):
            raise PrecisionRefused(
                "DeBERTa-v3 trains in FP32 (PRD §7.2): its disentangled attention is numerically "
                "fragile under reduced precision. Pass --allow-reduced-precision to run the "
                "bf16 ablation deliberately."
            )
        if self.scanner_features and self.findings is None:
            raise ValueError(
                "--scanner-features needs --findings <baseline_raw.json>: without the record-id "
                "keyed findings the flag would silently train the plain model (F3)"
            )
        if self.findings is not None and not self.scanner_features:
            raise ValueError("--findings is only read with --scanner-features (ablation row 9)")
        if self.fold == "all" and not self.epochs_explicit:
            raise ValueError(
                "--fold all has no held-out fold to early-stop against, so it needs the "
                "CV-selected epoch count passed explicitly with --epochs"
            )

    def label_map(self) -> dict[int, int]:
        """Three-class ids to the training head's ids. Ablation 12 collapses the two benigns."""
        if self.num_labels == 2:
            return {
                LABEL2ID["benign"]: 0,
                LABEL2ID["imperative_benign"]: 0,
                LABEL2ID["poisoned"]: 1,
            }
        return {i: i for i in ID2LABEL}

    def id2label(self) -> dict[int, str]:
        return dict(TWO_CLASS_ID2LABEL) if self.num_labels == 2 else dict(ID2LABEL)

    def model_version(self) -> str:
        return f"{self.profile}@{git_sha()}"

    def training_command(self, corpus: Path, out_dir: Path) -> str:
        """The exact command that reproduces this artefact. The model card publishes it."""
        parts = [
            "python -m sieve.model.train",
            "--config bench/train.yaml",
            f"--profile {self.profile}",
            f"--fold {self.fold}",
            f"--seed {self.seed}",
        ]
        if self.epochs_explicit:
            parts.append(f"--epochs {self.epochs}")
        if self.num_labels != 3:
            parts.append(f"--num-labels {self.num_labels}")
        if self.mode != "full":
            parts.append(f"--mode {self.mode}")
        if self.scanner_features:
            parts.append("--scanner-features")
            parts.append(f"--findings {self.findings.as_posix() if self.findings else ''}")
        if self.allow_reduced_precision:
            parts.append("--allow-reduced-precision")
        parts.append(f"--corpus {corpus.as_posix()}")
        parts.append(f"--out {out_dir.as_posix()}")
        return " ".join(parts)


def load_profile(
    config_path: Path,
    profile: str,
    *,
    fold: Fold,
    seed: int,
    mode: Mode = "full",
    num_labels: int = 3,
    scanner_features: bool = False,
    findings: Path | None = None,
    epochs: int | None = None,
    allow_reduced_precision: bool = False,
    allow_dirty: bool = False,
) -> TrainConfig:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    profiles = raw.get("profiles") or {}
    if profile not in profiles:
        raise KeyError(f"no profile {profile!r} in {config_path}; have {sorted(profiles)}")
    fields = dict(profiles[profile])
    if epochs is not None:
        fields["epochs"] = epochs
    return TrainConfig(
        **fields,
        seed=seed,
        fold=fold,
        mode=mode,
        num_labels=num_labels,
        scanner_features=scanner_features,
        findings=findings,
        profile=profile,
        allow_reduced_precision=allow_reduced_precision,
        allow_dirty=allow_dirty,
        epochs_explicit=epochs is not None,
    )


def default_corpus(config_path: Path) -> Path:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return Path(str(raw.get("corpus", "bench/data/labelled.jsonl")))


# --------------------------------------------------------------- payload survival (PRD §22)


def _normalise(text: str) -> str:
    """Compare what the encoder sees: NFKC, invisible format characters and whitespace gone.

    DeBERTa-v3's normaliser applies NFKC (a fullwidth comma becomes ",") and drops Unicode
    format characters (category Cf: zero-width and bidi controls). Comparing with them still in
    place reported every zero-width and bidi concealment row, and every real description with
    fullwidth punctuation, as truncated when nothing was cut.
    """
    folded = unicodedata.normalize("NFKC", text)
    return "".join(c for c in folded if not c.isspace() and unicodedata.category(c) != "Cf").lower()


def _field_fragments(record: CorpusRecord, field: EvidenceField) -> list[str]:
    """The pieces of `field` that `serialise` actually emits, as they appear in the text."""
    tool = record.tool
    if field == "name":
        return [tool.name]
    if field == "title":
        return [tool.title] if tool.title else []
    if field == "description":
        return [tool.description] if tool.description else []
    if field == "inputSchema":
        return param_lines(tool.inputSchema)
    if field == "outputSchema":
        return param_lines(tool.outputSchema or {})
    annotations = tool.annotations or {}
    return [str(k) for k in annotations] + [v for v in annotations.values() if isinstance(v, str)]


def payload_survival_failures(
    records: Sequence[CorpusRecord],
    tokenizer: Any,
    *,
    max_length: int,
    mode: Mode = "full",
    scanner_findings_by_id: dict[str, list[ScannerFinding]] | None = None,
) -> list[tuple[str, EvidenceField]]:
    """`(record id, field)` for every payload field TRUNCATED away at `max_length`.

    Compared after `_normalise`, because a wordpiece decode re-spaces punctuation and the
    encoder's own normaliser folds and drops characters. The unknown token is removed from the
    decode first: DeBERTa-v3 encodes a bidi control as `[UNK]`, and an invisible control char is
    not payload text. A fragment the serialisation never carried in the first place (an
    `outputSchema` payload, or anything but the description under `description_only`) is
    skipped: that is a coverage question for §11.2, and folding it in here would make ablation
    3 unrunnable. A *visible* character the tokenizer cannot encode still shows up as a
    failure, which is the behaviour we want: the encoder never saw it.
    """
    unknown = getattr(tokenizer, "unk_token", None) or ""
    failures: list[tuple[str, EvidenceField]] = []
    for record in records:
        if not record.payload_fields:
            continue
        findings = (scanner_findings_by_id or {}).get(record.id)
        text = serialise(record.to_tool_record(), mode=mode, scanner_findings=findings)
        full = _normalise(text)
        ids = tokenizer(text, truncation=True, max_length=max_length)["input_ids"]
        decoded = tokenizer.decode(ids, skip_special_tokens=False)
        kept = _normalise(decoded.replace(unknown, "") if unknown else decoded)
        for field in record.payload_fields:
            raw = [f for f in _field_fragments(record, field) if f in text]
            # A fragment made only of invisible characters normalises to "": the encoder sees
            # none of it, so the row's label rests on evidence the model never gets.
            invisible = any(f.strip() and not _normalise(f) for f in raw)
            fragments = [_normalise(f) for f in _field_fragments(record, field)]
            carried = [f for f in fragments if f and f in full]
            if invisible or (carried and any(f not in kept for f in carried)):
                failures.append((record.id, field))
    return failures


# ------------------------------------------------------------------------------ training


def scanner_findings_for(config: TrainConfig) -> dict[str, list[ScannerFinding]] | None:
    """The findings a run serialises with: only under `--scanner-features` (ablation row 9)."""
    if not config.scanner_features or config.findings is None:
        return None
    return load_scanner_findings(config.findings)


def file_sha256(path: Path) -> str:
    """SHA-256 of the file's bytes: pins the exact corpus an artefact was trained on."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


TRAINING_LIBRARIES = ("torch", "transformers", "accelerate", "datasets", "numpy", "tokenizers")


def library_versions() -> dict[str, str]:
    """Installed versions of everything that can change a training run's numbers."""
    versions = {"python": platform.python_version()}
    for name in TRAINING_LIBRARIES:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "absent"
    return versions


def _class_weights(labels: Sequence[int], num_labels: int) -> list[float]:
    counts = np.bincount(np.asarray(labels, dtype=np.int64), minlength=num_labels).astype(float)
    counts[counts == 0] = 1.0
    weights = counts.sum() / (num_labels * counts)
    return [float(w) for w in weights]


def _logits_and_labels(trainer: Any, dataset: Any) -> tuple[npt.NDArray[Any], npt.NDArray[Any]]:
    output = trainer.predict(dataset)
    return np.asarray(output.predictions, dtype=np.float32), np.asarray(
        output.label_ids, dtype=np.int64
    )


def _write_npy(path: Path, array: npt.NDArray[Any]) -> None:
    with path.open("wb") as handle:
        np.save(handle, array)


def training_arguments(
    config: TrainConfig, checkpoints: Path, *, has_early: bool, **overrides: Any
) -> Any:
    """The Trainer settings for one run. `overrides` exist for tests only."""
    import torch
    from transformers import TrainingArguments

    settings: dict[str, Any] = {
        "output_dir": str(checkpoints),
        "learning_rate": config.lr,
        "warmup_ratio": config.warmup_ratio,
        "per_device_train_batch_size": config.batch_size,
        # Evaluation keeps no activations for a backward pass, so twice the batch fits (B5).
        "per_device_eval_batch_size": 2 * config.batch_size,
        "num_train_epochs": config.epochs,
        "eval_strategy": "epoch" if has_early else "no",
        "save_strategy": "epoch" if has_early else "no",
        "save_total_limit": 1,
        # B2: checkpoints exist only to reload the best epoch at the end, and nothing ever
        # resumes from one, so optimizer and scheduler state (2x the weights) is dead weight.
        "save_only_model": True,
        "load_best_model_at_end": has_early,
        "metric_for_best_model": "macro_f1",
        "greater_is_better": True,
        "seed": config.seed,
        "data_seed": config.seed,
        "fp16": False,
        "bf16": config.precision == "bf16",
        # Non-reentrant: DeBERTa-v2 feeds one relative-position tensor to every layer, and the
        # reentrant implementation (transformers' default) backwards through it twice and dies.
        "gradient_checkpointing": config.gradient_checkpointing,
        "gradient_checkpointing_kwargs": {"use_reentrant": False}
        if config.gradient_checkpointing
        else None,
        # Length-grouped batches (B1): ~1.85x fewer padded tokens per epoch at 512 (plan §7.3).
        # No `length` column: in transformers 4.57.6 `_get_dataloader` strips every column the
        # model's forward does not take BEFORE the sampler is built, so the sampler would never
        # see one; `LengthGroupedSampler` then measures `input_ids` itself, the same numbers.
        "group_by_length": True,
        "use_cpu": not torch.cuda.is_available(),
        "report_to": [],
        "disable_tqdm": True,
        "logging_strategy": "epoch",
    }
    settings.update(overrides)
    return TrainingArguments(**settings)


def collator(tokenizer: Any) -> Any:
    """Dynamic padding to the longest row of each batch, rounded up to a multiple of 8.

    Multiples of 8 keep the matmul shapes tensor-core friendly (B5); the extra pad is < 8 tokens.
    """
    from transformers import DataCollatorWithPadding

    return DataCollatorWithPadding(tokenizer=tokenizer, pad_to_multiple_of=8)


def run(config: TrainConfig, corpus_path: Path, out_dir: Path) -> Path:
    """Train one (profile, fold, seed) and write the artefact directory. Returns `out_dir`."""
    import torch
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        EarlyStoppingCallback,
        Trainer,
        set_seed,
    )

    from sieve.eval.metrics import macro_f1

    require_clean_tree(config.profile, allow_dirty=config.allow_dirty)
    started = time.monotonic()
    set_seed(config.seed)
    # cuDNN's autotuner picks kernels by timing, so two runs can pick different ones. Residual
    # non-determinism (DeBERTa's backward pass) is measured by `make gpu-bench`, not hidden.
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False

    records = load_corpus(corpus_path)
    dropped = sum(1 for r in records if r.split != "dev")
    log.info("dropped %d row(s) whose split is not 'dev'", dropped)

    tokenizer: Any = AutoTokenizer.from_pretrained(config.base_model, revision=config.revision)  # type: ignore[no-untyped-call]
    vocab_size = add_markers(tokenizer)
    if "deberta" in config.base_model.lower():
        log.info(TOKENIZER_NOTE)

    findings = scanner_findings_for(config)
    failures = payload_survival_failures(
        [r for r in records if r.split == "dev"],
        tokenizer,
        max_length=config.max_length,
        mode=config.mode,
        scanner_findings_by_id=findings,
    )
    if failures:
        raise ValueError(
            f"payload fields truncated at max_length={config.max_length}, or holding characters "
            f"its tokenizer cannot encode: {failures}"
        )

    if config.fold == "all":
        splits = build_splits(
            records, fold=-1, mode=config.mode, seed=config.seed, scanner_findings_by_id=findings
        )
        train_ds = splits["train"]
        early_ds = splits["early_stop"]
        # `--fold all` trains on every dev server: fold -1 holds nothing out, so fold the
        # early-stopping slice back in and run the CV-selected epoch count with no callback.
        from datasets import concatenate_datasets  # type: ignore[import-untyped]

        train_ds = concatenate_datasets([train_ds, early_ds])
        early_ds = None
        oof_ds = None
    else:
        splits = build_splits(
            records,
            fold=config.fold,
            mode=config.mode,
            seed=config.seed,
            scanner_findings_by_id=findings,
        )
        train_ds, early_ds, oof_ds = splits["train"], splits["early_stop"], splits["oof"]

    mapping = config.label_map()
    # Plain values only: `datasets` fingerprints `encode` by pickling what it closes over, and
    # under `python -m` the config's class lives in `__main__`, which cannot be pickled.
    max_length = config.max_length

    def encode(batch: dict[str, list[Any]]) -> dict[str, Any]:
        encoded = tokenizer(batch["text"], truncation=True, max_length=max_length)
        encoded["labels"] = [mapping[int(x)] for x in batch["label"]]
        return dict(encoded)

    columns = ["id", "text", "label", "difficulty", "concealment", "source", "server_name", "fold"]
    tokenised = {
        name: ds.map(encode, batched=True, remove_columns=columns)
        for name, ds in (("train", train_ds), ("early_stop", early_ds), ("oof", oof_ds))
        if ds is not None
    }

    model = AutoModelForSequenceClassification.from_pretrained(
        config.base_model,
        revision=config.revision,
        num_labels=config.num_labels,
        id2label=config.id2label(),
        label2id={v: k for k, v in config.id2label().items()},
    )
    model.resize_token_embeddings(vocab_size)

    head_labels = list(config.id2label().values())

    def compute_metrics(pred: Any) -> dict[str, float]:
        predicted = np.asarray(pred.predictions).argmax(axis=1)
        truth = np.asarray(pred.label_ids)
        return {
            "macro_f1": macro_f1(
                [head_labels[i] for i in truth],
                [head_labels[i] for i in predicted],
                labels=head_labels,
            )
        }

    weights = (
        _class_weights([mapping[int(x)] for x in train_ds["label"]], config.num_labels)
        if config.class_weights
        else None
    )

    class WeightedTrainer(Trainer):
        def compute_loss(
            self,
            model: Any,
            inputs: dict[str, Any],
            return_outputs: bool = False,
            num_items_in_batch: Any = None,
        ) -> Any:
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            logits = outputs.get("logits")
            reduction = "sum" if num_items_in_batch is not None else "mean"
            loss_fct = torch.nn.CrossEntropyLoss(
                weight=None
                if weights is None
                else torch.tensor(weights, device=logits.device, dtype=logits.dtype),
                reduction=reduction,
            )
            loss = loss_fct(logits.view(-1, config.num_labels), labels.view(-1))
            if num_items_in_batch is not None:
                loss = loss / num_items_in_batch
            return (loss, outputs) if return_outputs else loss

    has_early = "early_stop" in tokenised
    checkpoints = out_dir / "_checkpoints"
    args = training_arguments(config, checkpoints, has_early=has_early)
    trainer = WeightedTrainer(
        model=model,
        args=args,
        train_dataset=tokenised["train"],
        eval_dataset=tokenised.get("early_stop"),
        processing_class=tokenizer,
        data_collator=collator(tokenizer),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=config.patience)]
        if has_early
        else None,
    )
    cuda = torch.cuda.is_available()
    if cuda:
        torch.cuda.reset_peak_memory_stats()
    train_output = trainer.train()
    train_seconds = float(train_output.metrics["train_runtime"])
    # Real (unpadded) tokens the optimiser saw: every training row once per epoch run.
    real_tokens = sum(len(ids) for ids in tokenised["train"]["input_ids"]) * trainer.state.epoch

    out_dir.mkdir(parents=True, exist_ok=True)
    trainer.save_model(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))

    per_epoch = [
        float(entry["eval_macro_f1"])
        for entry in trainer.state.log_history
        if "eval_macro_f1" in entry
    ]
    if has_early:
        early_logits, early_labels = _logits_and_labels(trainer, tokenised["early_stop"])
        _write_npy(out_dir / "early_stop_logits.npy", early_logits)
        _write_npy(out_dir / "early_stop_labels.npy", early_labels)
    if oof_ds is not None:
        oof_logits, oof_labels = _logits_and_labels(trainer, tokenised["oof"])
        _write_npy(out_dir / "oof_logits.npy", oof_logits)
        _write_npy(out_dir / "oof_labels.npy", oof_labels)
        (out_dir / "oof_ids.json").write_text(
            json.dumps(list(oof_ds["id"]), indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    metrics: dict[str, Any] = {
        "profile": config.profile,
        "base_model": config.base_model,
        "fold": config.fold,
        "seed": config.seed,
        "mode": config.mode,
        "num_labels": config.num_labels,
        "warmup_ratio": config.warmup_ratio,
        "precision": config.precision,
        # Read off the trained model, not the config: proof the flag reached the Trainer.
        "gradient_checkpointing": bool(getattr(trainer.model, "is_gradient_checkpointing", False)),
        "epochs_requested": config.epochs,
        "early_stop_macro_f1_per_epoch": per_epoch,
        "best_epoch": (1 + int(np.argmax(per_epoch))) if per_epoch else None,
        "rows_dropped_not_dev": dropped,
        "train_rows": len(tokenised["train"]),
        "early_stop_rows": len(tokenised["early_stop"]) if has_early else 0,
        "oof_rows": len(tokenised["oof"]) if oof_ds is not None else 0,
        "wall_clock_seconds": round(time.monotonic() - started, 3),
        # Provenance (training plan A5): what exactly produced these numbers.
        "corpus_sha256": file_sha256(corpus_path),
        "git_sha": git_sha(short=False),
        "git_dirty": bool(dirty_paths()),
        "library_versions": library_versions(),
        "base_model_revision": config.revision,
        "best_global_step": trainer.state.best_global_step,
        "profile_variant": config.variant or config.profile,
        "cudnn_deterministic": bool(torch.backends.cudnn.deterministic),
        # Throughput and memory (B4): what `make gpu-bench` and the §7.2 budget compare against.
        "eval_loss_per_epoch": [
            float(entry["eval_loss"]) for entry in trainer.state.log_history if "eval_loss" in entry
        ],
        "train_seconds": train_seconds,
        "seconds_per_step": train_seconds / max(trainer.state.global_step, 1),
        "tokens_per_second": float(real_tokens / train_seconds) if train_seconds else 0.0,
        "peak_cuda_mem_bytes": int(torch.cuda.max_memory_allocated()) if cuda else None,
    }
    _write_json(out_dir / "metrics.json", metrics)
    _write_json(
        out_dir / "sieve_config.json",
        {
            "model_version": config.model_version(),
            "base_model": config.base_model,
            "training_command": config.training_command(corpus_path, out_dir),
            "fold": config.fold,
            "seed": config.seed,
            "mode": config.mode,
            "num_labels": config.num_labels,
            "max_length": config.max_length,
            "temperature": None,
            "t_low": None,
            "t_high": None,
        },
    )
    shutil.rmtree(checkpoints, ignore_errors=True)
    return out_dir


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )


def _fold(value: str) -> Fold:
    if value == "all":
        return "all"
    fold = int(value)
    if not 0 <= fold <= 4:
        raise argparse.ArgumentTypeError("--fold takes 0-4 or the literal 'all'")
    return fold


def main(argv: Sequence[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(prog="python -m sieve.model.train")
    parser.add_argument("--config", type=Path, default=Path("bench/train.yaml"))
    parser.add_argument("--profile", required=True)
    parser.add_argument("--fold", required=True, type=_fold)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--num-labels", type=int, default=3, choices=(2, 3))
    parser.add_argument("--mode", default="full", choices=("full", "description_only"))
    parser.add_argument("--scanner-features", action="store_true")
    parser.add_argument(
        "--findings", type=Path, default=None, help="baseline_raw.json for --scanner-features"
    )
    parser.add_argument("--allow-reduced-precision", action="store_true")
    parser.add_argument(
        "--allow-dirty", action="store_true", help="train from uncommitted code (tests only)"
    )
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    ns = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    config = load_profile(
        ns.config,
        ns.profile,
        fold=ns.fold,
        seed=ns.seed,
        mode=ns.mode,
        num_labels=ns.num_labels,
        scanner_features=ns.scanner_features,
        findings=ns.findings,
        epochs=ns.epochs,
        allow_reduced_precision=ns.allow_reduced_precision,
        allow_dirty=ns.allow_dirty,
    )
    corpus = ns.corpus if ns.corpus is not None else default_corpus(ns.config)
    return run(config, corpus, ns.out)


__all__ = [
    "LABELS",
    "Label",
    "PrecisionRefused",
    "TrainConfig",
    "load_profile",
    "main",
    "payload_survival_failures",
    "run",
]

if __name__ == "__main__":  # pragma: no cover
    main()
