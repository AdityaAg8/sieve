"""Per-fold encoder training. Reads `split == "dev"` rows and nothing else.

There is no train/val/test run here and no `test_logits.npy` in this design: a fold's held-out
servers produce `oof_logits.npy`, early stopping watches a slice of the fold's *training*
servers, and the holdout is reachable only from `bench/eval_final.py` (PRD §11.6).
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import yaml
from pydantic import BaseModel, ConfigDict, Field

from sieve.model.data import ID2LABEL, LABEL2ID, add_markers, build_splits, load_corpus
from sieve.model.version import git_sha
from sieve.serialise import param_lines, serialise
from sieve.types import LABELS, CorpusRecord, EvidenceField, Label

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
    scanner_features: bool = False
    profile: str = "custom"
    allow_reduced_precision: bool = False
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
    epochs: int | None = None,
    allow_reduced_precision: bool = False,
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
        profile=profile,
        allow_reduced_precision=allow_reduced_precision,
        epochs_explicit=epochs is not None,
    )


def default_corpus(config_path: Path) -> Path:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return Path(str(raw.get("corpus", "bench/data/labelled.jsonl")))


# --------------------------------------------------------------- payload survival (PRD §22)


def _normalise(text: str) -> str:
    return "".join(text.split()).lower()


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
) -> list[tuple[str, EvidenceField]]:
    """`(record id, field)` for every payload field TRUNCATED away at `max_length`.

    Compared after whitespace-stripping and lower-casing, because a wordpiece decode re-spaces
    punctuation. A fragment the serialisation never carried in the first place (an
    `outputSchema` payload, or anything but the description under `description_only`) is
    skipped: that is a coverage question for §11.2, and folding it in here would make ablation
    3 unrunnable. A fragment the tokenizer drops entirely — an unknown codepoint in a
    concealment record — does show up, which is the behaviour we want.
    """
    failures: list[tuple[str, EvidenceField]] = []
    for record in records:
        if not record.payload_fields:
            continue
        text = serialise(record.to_tool_record(), mode=mode)
        full = _normalise(text)
        ids = tokenizer(text, truncation=True, max_length=max_length)["input_ids"]
        kept = _normalise(tokenizer.decode(ids, skip_special_tokens=False))
        for field in record.payload_fields:
            fragments = [_normalise(f) for f in _field_fragments(record, field)]
            carried = [f for f in fragments if f and f in full]
            if carried and any(f not in kept for f in carried):
                failures.append((record.id, field))
    return failures


# ------------------------------------------------------------------------------ training


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


def run(config: TrainConfig, corpus_path: Path, out_dir: Path) -> Path:
    """Train one (profile, fold, seed) and write the artefact directory. Returns `out_dir`."""
    import torch
    from transformers import (
        AutoModelForSequenceClassification,
        AutoTokenizer,
        DataCollatorWithPadding,
        EarlyStoppingCallback,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    from sieve.eval.metrics import macro_f1

    started = time.monotonic()
    set_seed(config.seed)

    records = load_corpus(corpus_path)
    dropped = sum(1 for r in records if r.split != "dev")
    log.info("dropped %d row(s) whose split is not 'dev'", dropped)

    tokenizer: Any = AutoTokenizer.from_pretrained(config.base_model)  # type: ignore[no-untyped-call]
    vocab_size = add_markers(tokenizer)

    failures = payload_survival_failures(
        [r for r in records if r.split == "dev"],
        tokenizer,
        max_length=config.max_length,
        mode=config.mode,
    )
    if failures:
        raise ValueError(f"payload fields truncated at max_length={config.max_length}: {failures}")

    if config.fold == "all":
        splits = build_splits(records, fold=-1, mode=config.mode, seed=config.seed)
        train_ds = splits["train"]
        early_ds = splits["early_stop"]
        # `--fold all` trains on every dev server: fold -1 holds nothing out, so fold the
        # early-stopping slice back in and run the CV-selected epoch count with no callback.
        from datasets import concatenate_datasets  # type: ignore[import-untyped]

        train_ds = concatenate_datasets([train_ds, early_ds])
        early_ds = None
        oof_ds = None
    else:
        splits = build_splits(records, fold=config.fold, mode=config.mode, seed=config.seed)
        train_ds, early_ds, oof_ds = splits["train"], splits["early_stop"], splits["oof"]

    mapping = config.label_map()

    def encode(batch: dict[str, list[Any]]) -> dict[str, Any]:
        encoded = tokenizer(batch["text"], truncation=True, max_length=config.max_length)
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
    args = TrainingArguments(
        output_dir=str(checkpoints),
        learning_rate=config.lr,
        warmup_ratio=config.warmup_ratio,
        per_device_train_batch_size=config.batch_size,
        per_device_eval_batch_size=config.batch_size,
        num_train_epochs=config.epochs,
        eval_strategy="epoch" if has_early else "no",
        save_strategy="epoch" if has_early else "no",
        save_total_limit=1,
        load_best_model_at_end=has_early,
        metric_for_best_model="macro_f1",
        greater_is_better=True,
        seed=config.seed,
        data_seed=config.seed,
        fp16=False,
        bf16=(config.precision == "bf16"),
        use_cpu=not torch.cuda.is_available(),
        report_to=[],
        disable_tqdm=True,
        logging_strategy="epoch",
    )
    trainer = WeightedTrainer(
        model=model,
        args=args,
        train_dataset=tokenised["train"],
        eval_dataset=tokenised.get("early_stop"),
        processing_class=tokenizer,
        data_collator=DataCollatorWithPadding(tokenizer=tokenizer),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=config.patience)]
        if has_early
        else None,
    )
    trainer.train()

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
        "epochs_requested": config.epochs,
        "early_stop_macro_f1_per_epoch": per_epoch,
        "best_epoch": (1 + int(np.argmax(per_epoch))) if per_epoch else None,
        "rows_dropped_not_dev": dropped,
        "train_rows": len(tokenised["train"]),
        "early_stop_rows": len(tokenised["early_stop"]) if has_early else 0,
        "oof_rows": len(tokenised["oof"]) if oof_ds is not None else 0,
        "wall_clock_seconds": round(time.monotonic() - started, 3),
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
    parser.add_argument("--allow-reduced-precision", action="store_true")
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
        epochs=ns.epochs,
        allow_reduced_precision=ns.allow_reduced_precision,
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
