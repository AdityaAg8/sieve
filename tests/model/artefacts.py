"""Build a real (tiny) model artefact with the lane's own writers.

`sieve_config.json` is never hand-written: the directory is produced by `sieve.model.train`
and `sieve.model.export`, and the operating point is written through the same file those
writers own. Kept here rather than inside a test module so the integration tests can reuse
the exact artefact L3's own tests load.
"""

from __future__ import annotations

import json
from pathlib import Path

#: The operating point written into the trained artefact. `sieve.model.calibrate` writes
#: these keys for real; a two-epoch model on mini-50 has nothing to calibrate, so the same
#: keys are set explicitly -- through the artefact's own config file, never around it.
CALIBRATION: dict[str, float] = {
    "temperature": 1.25,
    "t_low": 0.2,
    "t_high": 0.8,
    "val_recall_at_t_low": 0.99,
    "val_precision_at_t_high": 0.96,
    "routing_rate": 0.2,
}


def build_calibrated_tiny(root: Path) -> tuple[Path, Path]:
    """`(torch_dir, onnx_dir)` for a trained tiny model with an operating point written in."""
    from sieve.model.data import load_corpus
    from sieve.model.export import export
    from sieve.model.train import main as train_main

    records = load_corpus(Path("fixtures/corpus/mini-50.jsonl"))
    servers = sorted({r.server_name for r in records})
    fold_of = {name: i % 5 for i, name in enumerate(servers)}
    corpus = root / "corpus.jsonl"
    corpus.write_text(
        "".join(
            r.model_copy(update={"split": "dev", "fold": fold_of[r.server_name]}).model_dump_json()
            + "\n"
            for r in records
        ),
        encoding="utf-8",
        newline="\n",
    )
    torch_dir = root / "tiny-f0-s0"
    train_main(
        [
            "--config", "bench/train.yaml", "--profile", "tiny", "--fold", "0",
            "--seed", "0", "--corpus", str(corpus), "--out", str(torch_dir),
        ]
    )  # fmt: skip
    onnx_dir = export(torch_dir, root / "onnx")
    for directory in (torch_dir, onnx_dir):
        path = directory / "sieve_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config.update(CALIBRATION)
        path.write_text(
            json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
    return torch_dir, onnx_dir
