"""`python -m bench.analysis.plan_evidence <subcommand>`; `all` runs every subcommand in order.

Each subcommand prints its numbers and writes them to `bench/data/external/plan_evidence/
summary_<subcommand>.json`. `pool` and `yara-rates` cache the pools the later subcommands read.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from bench.analysis.plan_evidence import CORPUS, WORK
from bench.analysis.plan_evidence.pool import Row

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _records(corpus: Path) -> list[Any]:
    from sieve.model.data import load_corpus

    return load_corpus(corpus)


def _dev(corpus: Path) -> list[Any]:
    return [r for r in _records(corpus) if r.split == "dev"]


def _save(work: Path, name: str, payload: Any) -> Any:
    work.mkdir(parents=True, exist_ok=True)
    (work / f"summary_{name}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return payload


def _load_pool(work: Path, name: str) -> list[Row]:
    path = work / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing: run the `pool` and `yara-rates` subcommands first")
    rows: list[Row] = json.loads(path.read_text(encoding="utf-8"))
    return rows


def cmd_pool(args: argparse.Namespace) -> Any:
    import pyarrow.parquet as pq

    from bench.analysis.plan_evidence.pool import extra_pool, main_pool
    from bench.collect.catalog import download
    from bench.collect.external import fetch_mtp

    tools_parquet = download()[0]
    main = main_pool(_records(args.corpus), pq.read_table(tools_parquet).to_pylist(), args.external)
    extra = extra_pool(args.external, fetch_mtp())
    args.work.mkdir(parents=True, exist_ok=True)
    for name, rows in (("pool", main), ("pool_extra", extra)):
        (args.work / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8"
        )
    from collections import Counter

    counts = Counter(f"{r['src']} / {r['cls']}" for r in main + extra)
    return {
        "main_pool": len(main),
        "extra_pool": len(extra),
        "by_source": dict(sorted(counts.items())),
    }


def cmd_yara_rates(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.yara import format_rates, rates, scan_flags

    out: dict[str, Any] = {}
    for name in ("pool", "pool_extra"):
        rows = _load_pool(args.work, name)
        flags = scan_flags(rows, args.work / "scan")
        for r, f in zip(rows, flags, strict=True):
            r["flag"] = f
        (args.work / f"{name}_scanned.json").write_text(
            json.dumps(rows, ensure_ascii=False), encoding="utf-8"
        )
        table = rates(rows, flags)
        tox = [r for r in rows if r["src"] == "mcptox"]
        for template in sorted({r["template"] for r in tox}):
            chosen = [r for r in tox if r["template"] == template]
            table[("mcptox", template)] = (sum(r["flag"] for r in chosen), len(chosen))
        for line in format_rates(table):
            print(line)
        out[name] = {f"{s} / {c}": {"flagged": f, "total": n} for (s, c), (f, n) in table.items()}
    return out


def cmd_lengths(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.text import (
        DEBERTA,
        MODERNBERT,
        full_text,
        length_only_auroc,
        length_summary,
        row_text,
        serialised_token_length,
        token_lengths,
        tokenizer,
    )

    records = _records(args.corpus)
    pool = _load_pool(args.work, "pool")
    out: dict[str, Any] = {}
    for model in (DEBERTA, MODERNBERT):
        tok = tokenizer(model)
        name = model.split("/")[1]
        out[f"{name} corpus all rows"] = length_summary(
            token_lengths(tok, [full_text(r) for r in records])
        )
        out[f"{name} catalog"] = length_summary(
            token_lengths(tok, [row_text(r) for r in pool if r["src"] == "catalog"])
        )
        out[f"{name} MCPTox description"] = length_summary(
            token_lengths(tok, [f"[DESC] {r['desc']}" for r in pool if r["src"] == "mcptox"])
        )
    dev = [r for r in records if r.split == "dev"]
    out["length-only probe (DeBERTa serialised tokens, dev)"] = length_only_auroc(
        dev, serialised_token_length(dev, tokenizer(DEBERTA))
    )
    return out


def cmd_survival(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.text import DEBERTA, MODERNBERT, survival, tokenizer

    records = _records(args.corpus)
    return {m.split("/")[1]: survival(records, tokenizer(m)) for m in (DEBERTA, MODERNBERT)}


def cmd_baselines_ood(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.baselines import report

    return report(
        _load_pool(args.work, "pool_scanned"), _load_pool(args.work, "pool_extra_scanned")
    )


def cmd_dedup_cross(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.dedup_cross import cross_counts, embed_text, group
    from bench.split.dedup import MiniLMEmbedder

    rows = [
        r
        for r in _load_pool(args.work, "pool") + _load_pool(args.work, "pool_extra")
        if group(r) is not None
    ]
    embeddings = MiniLMEmbedder().embed([embed_text(r) for r in rows])
    return cross_counts(rows, embeddings)


def cmd_bucketing_sim(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.stats import bucketing_sim
    from bench.analysis.plan_evidence.text import DEBERTA, serialised_token_length, tokenizer

    lengths = serialised_token_length(_dev(args.corpus), tokenizer(DEBERTA))
    return bucketing_sim([int(x) for x in lengths])


def cmd_tokenizer_parity(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.text import tokenizer_parity

    return tokenizer_parity(_records(args.corpus))


def cmd_early_stop_slices(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.text import early_stop_slices

    return early_stop_slices(_records(args.corpus))


def cmd_kappa_ci(args: argparse.Namespace) -> Any:
    from collections import Counter

    from bench.analysis.plan_evidence.stats import REAL_SOURCES, cp_table, kappa_half_width

    table = [
        {"disagreements": k, "rate": rate, "lower": lo, "upper": hi}
        for k, rate, lo, hi in cp_table()
    ]
    for row in table:
        print(
            f"{row['disagreements']}/104: {row['rate']:.1%} "
            f"[{row['lower']:.1%}, {row['upper']:.1%}]"
        )
    real = Counter(
        r.label for r in _dev(args.corpus) if r.source in REAL_SOURCES and r.label != "poisoned"
    )
    p = real["benign"] / sum(real.values())
    return {
        "clopper_pearson_104": table,
        "kappa_0.80_n50_half_width": kappa_half_width(0.80, 50, p),
        "benign_share_real_dev": p,
    }


def cmd_baseline_collisions(args: argparse.Namespace) -> Any:
    from bench.analysis.plan_evidence.stats import baseline_collisions

    return baseline_collisions(_dev(args.corpus))


def cmd_tstat_order(args: argparse.Namespace) -> Any:
    from dataclasses import asdict

    from bench.analysis.plan_evidence.stats import tstat_order

    return asdict(tstat_order(_dev(args.corpus)))


COMMANDS: dict[str, Callable[[argparse.Namespace], Any]] = {
    "pool": cmd_pool,
    "yara-rates": cmd_yara_rates,
    "lengths": cmd_lengths,
    "survival": cmd_survival,
    "baselines-ood": cmd_baselines_ood,
    "dedup-cross": cmd_dedup_cross,
    "bucketing-sim": cmd_bucketing_sim,
    "tokenizer-parity": cmd_tokenizer_parity,
    "early-stop-slices": cmd_early_stop_slices,
    "kappa-ci": cmd_kappa_ci,
    "baseline-collisions": cmd_baseline_collisions,
    "tstat-order": cmd_tstat_order,
}


def main(argv: list[str] | None = None) -> int:
    from bench.collect.external import EXTERNAL_ROOT

    parser = argparse.ArgumentParser(
        prog="python -m bench.analysis.plan_evidence", description=__doc__
    )
    parser.add_argument("command", choices=[*COMMANDS, "all"])
    parser.add_argument("--corpus", type=Path, default=CORPUS)
    parser.add_argument("--external", type=Path, default=EXTERNAL_ROOT)
    parser.add_argument("--work", type=Path, default=WORK)
    args = parser.parse_args(argv)
    for name in COMMANDS if args.command == "all" else [args.command]:
        result = _save(args.work, name, COMMANDS[name](args))
        print(
            f"== {name}\n{json.dumps(result, indent=2, ensure_ascii=False, default=str)[:4000]}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
