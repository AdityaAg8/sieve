"""The mutation plan (PRD §9.4, §9.5): which real host receives which payload spec.

A plan row is exactly what `bench.generate.mutate` consumes: `host_id` plus the `PayloadSpec`
fields. The poisoned rows cross every sub-label (singly and in the common pairs), the three
semantic tiers, the concealment attribute and four injection sites, so the bench can report
each of them. Hosts are real rows only. Benign and imperative mutations take `benign` hosts,
so the added sentence alone decides the class; poisoned mutations take either real class
(§9.5 step 1), which is what puts a payload inside an already-directive description.

Hosts are dealt round-robin across servers so no server carries a cluster of mutations, and a
host whose payload-bearing text is already long is skipped: a payload pushed past DeBERTa's
512 tokens is refused by `sieve.model.train` rather than trained on.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from sieve.serialise import param_lines
from sieve.types import CorpusRecord, Label, SubLabel

SINGLES: tuple[tuple[SubLabel, ...], ...] = (
    ("exfiltration",),
    ("instruction_override",),
    ("tool_shadowing",),
    ("privilege_escalation",),
    ("secret_solicitation",),
    ("command_execution",),
    ("concealment",),
)
PAIRS: tuple[tuple[SubLabel, ...], ...] = (
    ("exfiltration", "concealment"),
    ("secret_solicitation", "exfiltration"),
    ("instruction_override", "concealment"),
    ("tool_shadowing", "exfiltration"),
    ("command_execution", "concealment"),
)
POISONED_SUB_LABELS: tuple[tuple[SubLabel, ...], ...] = SINGLES + PAIRS
#: Every single four times, then every pair once: singles are the bulk, pairs still recur.
_SUB_LABEL_CYCLE = SINGLES * 4 + PAIRS

TIERS = ("easy", "medium", "hard")
TIER_WEIGHTS = (0.3, 0.4, 0.3)
CONCEALMENTS = ("none", "html_comment", "zero_width", "bidi", "whitespace")
CONCEALMENT_WEIGHTS = (0.72, 0.07, 0.07, 0.07, 0.07)
POISONED_SITES: tuple[tuple[str, ...], ...] = (
    ("description",),
    ("inputSchema",),
    ("description", "inputSchema"),
    ("annotations",),
)
POISONED_SITE_WEIGHTS = (0.60, 0.28, 0.07, 0.05)
DIRECTIVE_SITES: tuple[tuple[str, ...], ...] = (("description",), ("inputSchema",))
IMPERATIVE_SITE_WEIGHTS = (0.75, 0.25)
BENIGN_SITE_WEIGHTS = (0.80, 0.20)

#: Description plus rendered parameter lines, in characters (~4 per token): room is left for a
#: payload of up to ~100 tokens, even zero-width-spaced, inside DeBERTa's 512.
HOST_BUDGET_CHARS = 1200
MAX_USES_PER_HOST = 2
DEFAULT_SEED = 42
REAL_SOURCES = frozenset({"public_repo", "wild_fp"})


def _payload_chars(record: CorpusRecord) -> int:
    tool = record.tool
    return len(tool.description or "") + sum(len(line) for line in param_lines(tool.inputSchema))


def _first_property(record: CorpusRecord) -> str | None:
    properties = record.tool.inputSchema.get("properties") or {}
    return next(iter(properties), None) if isinstance(properties, dict) else None


class _HostPicker:
    """Deals hosts round-robin over shuffled servers, never past `MAX_USES_PER_HOST`."""

    def __init__(
        self,
        records: Sequence[CorpusRecord],
        rng: np.random.Generator,
        uses: collections.Counter[str],
    ) -> None:
        by_server: dict[str, list[CorpusRecord]] = collections.defaultdict(list)
        for record in sorted(records, key=lambda r: r.id):
            if record.source in REAL_SOURCES and _payload_chars(record) <= HOST_BUDGET_CHARS:
                by_server[record.server_name].append(record)
        names = sorted(by_server)
        self.servers = [names[int(i)] for i in rng.permutation(len(names))]
        self.by_server = {
            s: [by_server[s][int(i)] for i in rng.permutation(len(by_server[s]))]
            for s in self.servers
        }
        self.uses = uses
        self.cursor = 0

    def pick(self, labels: frozenset[str], needs_property: bool) -> CorpusRecord:
        for step in range(len(self.servers)):
            server = self.servers[(self.cursor + step) % len(self.servers)]
            for host in self.by_server[server]:
                if host.label not in labels or self.uses[host.id] >= MAX_USES_PER_HOST:
                    continue
                if needs_property and _first_property(host) is None:
                    continue
                self.uses[host.id] += 1
                self.cursor = (self.cursor + step + 1) % len(self.servers)
                return host
        raise ValueError(f"no host left for labels={sorted(labels)} property={needs_property}")


def _row(
    host: CorpusRecord,
    label: Label,
    target_fields: Sequence[str],
    *,
    sub_labels: Sequence[str] = (),
    difficulty: str | None = None,
    concealment: str = "none",
) -> dict[str, Any]:
    return {
        "host_id": host.id,
        "label": label,
        "sub_labels": list(sub_labels),
        "difficulty": difficulty,
        "target_fields": list(target_fields),
        "property_name": _first_property(host) if "inputSchema" in target_fields else None,
        "concealment": concealment,
    }


def _choice(rng: np.random.Generator, options: Sequence[Any], weights: Sequence[float]) -> Any:
    return options[int(rng.choice(len(options), p=weights))]


def build_plan(
    records: Sequence[CorpusRecord],
    *,
    poisoned: int,
    imperative: int,
    benign: int,
    seed: int = DEFAULT_SEED,
    uses: collections.Counter[str] | None = None,
) -> list[dict[str, Any]]:
    """Plan rows for a model-authored payload file: poisoned, then imperative, then benign."""
    rng = np.random.default_rng(seed)
    picker = _HostPicker(records, rng, uses if uses is not None else collections.Counter())
    any_real = frozenset({"benign", "imperative_benign"})
    benign_only = frozenset({"benign"})
    rows: list[dict[str, Any]] = []
    for index in range(poisoned):
        site = _choice(rng, POISONED_SITES, POISONED_SITE_WEIGHTS)
        host = picker.pick(any_real, needs_property="inputSchema" in site)
        rows.append(
            _row(
                host,
                "poisoned",
                site,
                sub_labels=_SUB_LABEL_CYCLE[index % len(_SUB_LABEL_CYCLE)],
                difficulty=_choice(rng, TIERS, TIER_WEIGHTS),
                concealment=_choice(rng, CONCEALMENTS, CONCEALMENT_WEIGHTS),
            )
        )
    for label, count, weights in (
        ("imperative_benign", imperative, IMPERATIVE_SITE_WEIGHTS),
        ("benign", benign, BENIGN_SITE_WEIGHTS),
    ):
        for _ in range(count):
            site = _choice(rng, DIRECTIVE_SITES, weights)
            host = picker.pick(benign_only, needs_property="inputSchema" in site)
            rows.append(_row(host, label, site))  # type: ignore[arg-type]
    return rows


def human_plan(
    records: Sequence[CorpusRecord],
    payloads: Path,
    *,
    seed: int = DEFAULT_SEED,
    uses: collections.Counter[str] | None = None,
) -> list[dict[str, Any]]:
    """One description-site row per hand-authored payload, in the file's own order."""
    rng = np.random.default_rng(seed)
    picker = _HostPicker(records, rng, uses if uses is not None else collections.Counter())
    rows: list[dict[str, Any]] = []
    for line in payloads.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        host = picker.pick(frozenset({"benign", "imperative_benign"}), needs_property=False)
        rows.append(
            _row(
                host,
                "poisoned",
                ("description",),
                sub_labels=entry["sub_labels"],
                difficulty=entry["difficulty"],
            )
        )
    return rows


# ------------------------------------------------------------------------------------- v1
#
# Training plan E1 implements §5.3 items 1-3 and 7. The v0 plan above made the synthetic rows
# separable on style alone (payload length, last-sentence position, an annotations-only site,
# two-field payloads copied whole into both fields). v1 keeps every row id and its label spec
# (label, sub-labels, tier, concealment) and re-chooses only the host, the site where the site
# was annotations, and where in the host the payload goes.
#
# Concealment (§5.3 item 7) is two orthogonal things, both kept on every row: the
# `concealment` SUB-LABEL says the payload asks for secrecy ("don't tell the user"); the
# `concealment` ATTRIBUTE is the mechanical hiding applied to the text (zero-width,
# HTML comment, bidi, buried under blank lines). A row can have either, both or neither.

#: imperative_benign and benign share the directive cap; poisoned rows have their own.
V1_SERVER_CAP = {"directive": 3, "poisoned": 4}
V1_MAX_USES_PER_HOST = 2
V1_KNN = 8
#: The v0 poisoned site weights without annotations, renormalised.
V1_POISONED_SITES: tuple[tuple[str, ...], ...] = POISONED_SITES[:3]
V1_POISONED_SITE_WEIGHTS = tuple(
    w / sum(POISONED_SITE_WEIGHTS[:3]) for w in POISONED_SITE_WEIGHTS[:3]
)
POSITIONS = ("start", "middle", "end")
#: The register lint's length band (§5.3 item 6): a regenerated payload is 40-160 characters,
#: so a model-written proxy is clamped to it when matching lengths.
LINT_CHARS = (40, 160)
#: Joiner and concealment overhead added to a payload's length when matching.
PAYLOAD_SLACK_CHARS = 12
#: Planned first to last: imperative first, then poisoned (§5.3 item 1).
V1_ORDER: tuple[Label, ...] = ("imperative_benign", "benign", "poisoned")
_HOST_LABELS: dict[str, frozenset[str]] = {
    "imperative_benign": frozenset({"benign", "imperative_benign"}),
    # A benign paraphrase on an imperative host would make the row imperative.
    "benign": frozenset({"benign"}),
    "poisoned": frozenset({"benign", "imperative_benign"}),
}
#: The real rows whose host+payload length each label is matched to. Poisoned rows match the
#: whole non-poisoned real distribution, so length says nothing about "poisoned vs rest".
_TARGET_LABELS: dict[str, frozenset[str]] = {
    "imperative_benign": frozenset({"imperative_benign"}),
    "benign": frozenset({"benign"}),
    "poisoned": frozenset({"benign", "imperative_benign"}),
}

_SOFTEN_RULES: tuple[tuple[str, Any], ...] = (
    (r";\s+(\w)", lambda m: ". " + m.group(1).upper()),
    (r",?\s+rather than\b[^.!?]*", ""),
    (r",?\s+instead of\b[^.!?]*", ""),
    (r",?\s+since\b[^.!?]*", ""),
    (r"(^|[.!?]\s+)Since [^,]*,\s*(\w)", lambda m: m.group(1) + m.group(2).upper()),
    (r",\s+so\s+(?!that)[^.!?]*", ""),
)


def soften(payload: str) -> str:
    """The leakage audit's label-preserving clean-up of a v0 payload (2026-10-02, exp4).

    Keeps every directive and drops the generator's justification clauses ("rather than",
    "since", "instead of", "; ...", ", so ..."). Used only as the stand-in for a regenerated
    payload: for host matching, and for the CPU dry run.
    """
    text = payload
    for pattern, replacement in _SOFTEN_RULES:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _non_ascii(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


def _proxy(row: Mapping[str, Any]) -> str:
    """The text standing in for the row's payload: hand-written verbatim, model-written softened."""
    payload = str(row["payload"])
    return payload if row["generator_model"] == "human" else soften(payload)


def _proxy_chars(row: Mapping[str, Any]) -> int:
    n = len(_proxy(row))
    return n if row["generator_model"] == "human" else int(np.clip(n, *LINT_CHARS))


def _field_text(host: CorpusRecord, field: str, property_name: str | None) -> str | None:
    if field == "description":
        return host.tool.description
    properties = host.tool.inputSchema.get("properties") or {}
    prop = properties.get(property_name or "") if isinstance(properties, dict) else None
    return prop.get("description") if isinstance(prop, dict) else None


def _group(label: str) -> str:
    return "poisoned" if label == "poisoned" else "directive"


class _Once:
    """Hands `mutate` one fixed payload: the proxy, for the survival check of a candidate."""

    model_id = "plan-v1-proxy"

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def generate(self, spec: Any, host_description: str) -> str:
        return self.payload


def replan_v1(
    rows: Sequence[Mapping[str, Any]],
    records: Sequence[CorpusRecord],
    split_of: Mapping[str, str],
    *,
    seed: int,
    survives: Callable[[CorpusRecord], bool],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Re-plan `rows` (v0 plan rows plus `id`, `payload`, `generator_model`) onto new hosts.

    Per row, in the order imperative, benign, poisoned and within a label a seeded order:
    1. the row's split is the frozen split of its v0 host's server, and its new host is a real
       row of the allowed class (`_HOST_LABELS`) on a server of that same split;
    2. a target real row of the matching class (`_TARGET_LABELS`, same split) is drawn without
       replacement; hosts whose length plus the payload's is nearest the target's in log
       characters, with the target's non-ASCII bin when any such host is left, are kept;
    3. of the 8 nearest, the one most similar to the payload (TF-IDF cosine) is tried first, and
       a candidate is accepted only if `survives` (the 512-token check) passes with the payload
       in place;
    4. the payload position is start / middle / end, a seeded third each; middle only goes to
       hosts with an interior sentence boundary in every target field.

    Returns `{id: plan row}` in input order (placed rows only) and stats, whose `unplaced`
    lists every id that found no host. Reads only `split_of` (the frozen map) and the hosts'
    own labels; no evaluation data.
    """
    from bench.generate.mutate import PayloadSpec, mutate, sentence_count
    from bench.split.dedup import text_of

    rng = np.random.default_rng(seed)
    by_id = {r.id: r for r in records}
    real = sorted((r for r in records if r.source in REAL_SOURCES), key=lambda r: r.id)
    split = {r.id: split_of.get(r.server_name, "dev") for r in real}
    host_len = np.log(np.array([len(text_of(h)) for h in real], dtype=float))
    host_chars = np.exp(host_len)
    host_na = np.array([_non_ascii(text_of(h)) for h in real])
    has_prop = np.array([_first_property(h) is not None for h in real])
    descriptions = [h.tool.description or "" for h in real]
    vectoriser = TfidfVectorizer(sublinear_tf=True, stop_words="english").fit(
        descriptions + [_proxy(row) for row in rows]
    )
    host_vectors = vectoriser.transform(descriptions)
    uses: collections.Counter[str] = collections.Counter()
    per_server: dict[str, collections.Counter[str]] = {
        g: collections.Counter() for g in V1_SERVER_CAP
    }
    stats: dict[str, Any] = {
        "survival_reject": 0,
        "site_reassigned": 0,
        "middle_fallback": 0,
        "no_targets": [],
        "unplaced": [],
        "proxy_chars": {str(row["id"]): _proxy_chars(row) for row in rows},
    }
    placed: dict[str, dict[str, Any]] = {}

    for label in V1_ORDER:
        group_rows = sorted((r for r in rows if r["label"] == label), key=lambda r: str(r["id"]))
        if not group_rows:
            continue
        group = _group(label)
        splits = sorted(
            {split_of.get(by_id[str(r["host_id"])].server_name, "dev") for r in group_rows}
        )
        for split_name in splits:
            todo = [
                r
                for r in group_rows
                if split_of.get(by_id[str(r["host_id"])].server_name, "dev") == split_name
            ]
            in_split = np.array([split[h.id] == split_name for h in real])
            allowed = in_split & np.array([h.label in _HOST_LABELS[label] for h in real])
            targets = [
                h for h in real if split[h.id] == split_name and h.label in _TARGET_LABELS[label]
            ]
            if not targets:
                stats["no_targets"].append(f"{label}/{split_name}")
            targets = [targets[int(i)] for i in rng.permutation(len(targets))]
            for k, index in enumerate(rng.permutation(len(todo))):
                row = dict(todo[int(index)])
                fields = list(row["target_fields"])
                if "annotations" in fields:
                    fields = list(
                        V1_POISONED_SITES[
                            int(rng.choice(len(V1_POISONED_SITES), p=V1_POISONED_SITE_WEIGHTS))
                        ]
                    )
                    stats["site_reassigned"] += 1
                position = POSITIONS[int(rng.integers(len(POSITIONS)))]
                ok = allowed & np.array(
                    [
                        uses[h.id] < V1_MAX_USES_PER_HOST
                        and per_server[group][h.server_name] < V1_SERVER_CAP[group]
                        for h in real
                    ]
                )
                if "inputSchema" in fields:
                    ok &= has_prop
                if position == "middle":
                    roomy = np.array(
                        [
                            all(
                                sentence_count(_field_text(h, f, _first_property(h))) >= 2
                                for f in fields
                            )
                            for h in real
                        ]
                    )
                    if (ok & roomy).any():
                        ok &= roomy
                    else:
                        position = POSITIONS[2 * int(rng.integers(2))]
                        stats["middle_fallback"] += 1
                proxy = _proxy(row)
                slack = _proxy_chars(row) + PAYLOAD_SLACK_CHARS
                if targets:
                    target = text_of(targets[k % len(targets)])
                    if (ok & (host_na == _non_ascii(target))).any():
                        ok &= host_na == _non_ascii(target)
                    distance = np.abs(np.log(host_chars + slack) - np.log(len(target)))
                else:
                    distance = np.zeros(len(real))
                distance[~ok] = np.inf
                order = [
                    int(i) for i in np.argsort(distance, kind="stable") if np.isfinite(distance[i])
                ]
                proxy_vector = vectoriser.transform([proxy])
                chosen: dict[str, Any] | None = None
                while order and chosen is None:
                    near = order[:V1_KNN]
                    similarity = (host_vectors[near] @ proxy_vector.T).toarray().ravel()
                    best = near[int(np.argmax(similarity))]
                    order.remove(best)
                    host = real[best]
                    property_name = _first_property(host) if "inputSchema" in fields else None
                    insert_at, where = {}, {}
                    for field in fields:
                        n = sentence_count(_field_text(host, field, property_name))
                        if position == "start" or n == 0:
                            insert_at[field], where[field] = 0, "start" if n else position
                        elif position == "end":
                            insert_at[field], where[field] = n, "end"
                        else:
                            insert_at[field], where[field] = int(rng.integers(1, n)), "middle"
                    candidate = {
                        "host_id": host.id,
                        "label": row["label"],
                        "sub_labels": list(row["sub_labels"]),
                        "difficulty": row["difficulty"],
                        "target_fields": fields,
                        "property_name": property_name,
                        "concealment": row.get("concealment", "none"),
                        "insert_at": insert_at,
                        "position": where,
                        "split_payload": len(fields) == 2,
                    }
                    spec = PayloadSpec(**candidate, host_name=host.tool.name)
                    trial = mutate(host, spec, _Once(proxy), new_id=f"{row['id']}-trial")
                    if survives(trial):
                        chosen = candidate
                    else:
                        stats["survival_reject"] += 1
                if chosen is None:
                    stats["unplaced"].append(str(row["id"]))
                    continue
                uses[chosen["host_id"]] += 1
                per_server[group][by_id[chosen["host_id"]].server_name] += 1
                placed[str(row["id"])] = chosen

    stats["unplaced"].sort()
    stats["positions"] = dict(
        collections.Counter(w for p in placed.values() for w in p["position"].values())
    )
    ordered = {str(r["id"]): placed[str(r["id"])] for r in rows if str(r["id"]) in placed}
    return ordered, stats


# ------------------------------------------------------------------------------ v1 CLI

MODEL_PREFIX = "sb-syn-"
HUMAN_PREFIX = "sb-hum-"
DRYRUN_DIR = Path("bench/data/_dryrun")
KS_ALPHA = 0.05


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_rows(
    plan: Path, payloads: Path, human_plan_path: Path, human_payloads: Path
) -> list[dict[str, Any]]:
    """The v0 plan rows with their ids and payloads: `sb-syn-i` is plan row i, `sb-hum-i` hand
    payload i, exactly as `mutate --id-prefix` numbered them, so every id survives the re-plan."""
    rows: list[dict[str, Any]] = []
    model = json.loads(plan.read_text(encoding="utf-8"))
    written = _jsonl(payloads)
    if len(model) != len(written):
        raise ValueError(f"{plan} has {len(model)} rows but {payloads} {len(written)} payloads")
    for i, (row, payload) in enumerate(zip(model, written, strict=True)):
        if payload["plan_index"] != i:
            raise ValueError(f"{payloads}: plan_index {payload['plan_index']} at line {i}")
        rows.append(
            {
                **row,
                "id": f"{MODEL_PREFIX}{i:04d}",
                "payload": payload["payload"],
                "generator_model": payload["generator_model"],
            }
        )
    human = json.loads(human_plan_path.read_text(encoding="utf-8"))
    hand = _jsonl(human_payloads)
    if len(human) != len(hand):
        raise ValueError(
            f"{human_plan_path} has {len(human)} rows but {human_payloads} {len(hand)}"
        )
    for i, (row, entry) in enumerate(zip(human, hand, strict=True)):
        if (
            set(row["sub_labels"]) != set(entry["sub_labels"])
            or row["difficulty"] != entry["difficulty"]
        ):
            raise ValueError(f"{human_plan_path} row {i} does not match hand payload {i}")
        rows.append(
            {
                **row,
                "id": f"{HUMAN_PREFIX}{i:04d}",
                "payload": entry["payload"],
                "generator_model": "human",
            }
        )
    return rows


def _split_map(path: Path) -> dict[str, dict[str, Any]]:
    return dict(json.loads(path.read_text(encoding="utf-8"))["servers"])


#: Both headline architectures train at 512 tokens (plan D13), so a payload must survive BOTH
#: tokenizers. DeBERTa alone was checked until ModernBERT refused four rows at `make gpu-bench`
#: (2026-10-04): its tokenizer spends more tokens on the same text.
HEADLINE_PROFILES = ("deberta", "modernbert")


def _profile(config: Path, name: str = "deberta") -> dict[str, Any]:
    import yaml

    return dict(yaml.safe_load(config.read_text(encoding="utf-8"))["profiles"][name])


def _survival_check(
    config: Path, profiles: Sequence[str] = HEADLINE_PROFILES
) -> Callable[[CorpusRecord], bool]:
    """The real check (`sieve.model.train.payload_survival_failures`) under every headline
    profile's tokenizer and max_length, each pinned to the profile's revision."""
    from transformers import AutoTokenizer

    from sieve.model.data import add_markers
    from sieve.model.train import payload_survival_failures

    checks = []
    for name in profiles:
        profile = _profile(config, name)
        tokenizer = AutoTokenizer.from_pretrained(  # type: ignore[no-untyped-call]
            profile["base_model"], revision=profile["revision"]
        )
        add_markers(tokenizer)
        checks.append((tokenizer, int(profile["max_length"])))
    return lambda record: all(
        not payload_survival_failures([record], tokenizer, max_length=length)
        for tokenizer, length in checks
    )


def _token_lengths(records: Sequence[CorpusRecord], config: Path) -> Any:
    from bench.analysis.probes import _token_lengths as lengths

    return lengths(records, config)


def dry_run_records(
    rows: Sequence[Mapping[str, Any]],
    plan: Mapping[str, Mapping[str, Any]],
    hosts: Mapping[str, CorpusRecord],
    split_map: Mapping[str, Mapping[str, Any]],
    labels: Mapping[str, CorpusRecord],
) -> list[CorpusRecord]:
    """Each planned row built with its PROXY payload (the existing payload, softened), carrying
    the label fields the corpus already holds for that id. Diagnostic only (training plan E1)."""
    from bench.generate.mutate import PayloadSpec, mutate

    out = []
    for row in rows:
        planned = plan[str(row["id"])]
        host = hosts[planned["host_id"]]
        spec = PayloadSpec(
            **{k: v for k, v in planned.items() if k != "host_id"}, host_name=host.tool.name
        )
        record = mutate(host, spec, _Once(_proxy(row)), new_id=str(row["id"]))
        record.generator_model = str(row["generator_model"])
        known = labels.get(record.id)
        if known is not None:
            for name in ("label", "sub_labels", "difficulty", "labeller", "labelled_at"):
                setattr(record, name, getattr(known, name))
        entry = split_map.get(host.server_name, {"split": "dev", "fold": None})
        record.split, record.fold = entry["split"], entry["fold"]
        out.append(record)
    return out


def _ks(a: Sequence[int], b: Sequence[int]) -> dict[str, Any]:
    from scipy.stats import ks_2samp

    result = ks_2samp(a, b)
    return {
        "n_synthetic": len(a),
        "n_real": len(b),
        "statistic": round(float(result.statistic), 4),
        "p": round(float(result.pvalue), 4),
        "passed": bool(result.pvalue > KS_ALPHA),
    }


def dry_run_report(
    dry: Sequence[CorpusRecord],
    corpus: Sequence[CorpusRecord],
    *,
    survives: Callable[[CorpusRecord], bool],
    lengths: Callable[[Sequence[CorpusRecord]], Any] | None,
    stats: Mapping[str, Any],
) -> dict[str, Any]:
    """Survival failures, length KS tests against the real rows, and the C4 probes (not gated)."""
    from bench.analysis.leakage_probe import InsufficientStratum
    from bench.analysis.probes import run_all
    from bench.split.dedup import text_of

    failures = sorted(r.id for r in dry if not survives(r))
    real = [r for r in corpus if r.source in REAL_SOURCES]
    ks = {}
    for label, targets in _TARGET_LABELS.items():
        synthetic = [len(text_of(r)) for r in dry if r.label == label]
        reference = [len(text_of(r)) for r in real if r.label in targets]
        if synthetic and reference:
            ks[label] = _ks(synthetic, reference)
    replaced = {r.id: r for r in dry}
    candidate = [replaced.get(r.id, r) for r in corpus]
    try:
        probes = run_all(candidate, lengths=None if lengths is None else lengths(candidate))
    except InsufficientStratum as error:  # diagnostic only: say why there is no value
        probes = {"rows": "dev", "error": str(error)}
    return {
        "rows": len(dry),
        "survival_failures": failures,
        "ks_host_plus_payload_chars": ks,
        "probes_diagnostic": probes,
        "plan_stats": {k: v for k, v in stats.items() if k != "proxy_chars"},
        "passed": not failures and ks.get("imperative_benign", {}).get("passed", False),
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.generate.plan",
        description="Re-plan the synthetic rows onto matched hosts (training plan E1, §5.3).",
    )
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--split-map", type=Path, default=Path("bench/data/split_map.json"))
    parser.add_argument("--plan", type=Path, default=Path("bench/data/synthetic/plan.json"))
    parser.add_argument(
        "--payloads", type=Path, default=Path("bench/data/synthetic/payloads.jsonl")
    )
    parser.add_argument(
        "--human-plan", type=Path, default=Path("bench/data/synthetic/human_plan.json")
    )
    parser.add_argument(
        "--human-payloads", type=Path, default=Path("bench/generate/human_payloads.jsonl")
    )
    parser.add_argument("--config", type=Path, default=Path("bench/train.yaml"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=Path("bench/data/synthetic/plan_v1.json"))
    parser.add_argument(
        "--human-out", type=Path, default=Path("bench/data/synthetic/human_plan_v1.json")
    )
    parser.add_argument(
        "--dry-run",
        type=Path,
        nargs="?",
        const=DRYRUN_DIR,
        default=None,
        help="build the rows with the existing payloads softened and report; writes only here",
    )
    parser.add_argument("--no-length", action="store_true", help="skip the length probe (tests)")
    args = parser.parse_args(argv)
    if args.dry_run is not None and args.dry_run.name != DRYRUN_DIR.name:
        parser.error(f"--dry-run writes only into a directory named {DRYRUN_DIR.name}")

    corpus = [
        CorpusRecord.model_validate_json(line)
        for line in args.corpus.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    split_map = _split_map(args.split_map)
    rows = load_rows(args.plan, args.payloads, args.human_plan, args.human_payloads)
    survives = _survival_check(args.config)
    plan, stats = replan_v1(
        rows,
        corpus,
        {server: str(entry["split"]) for server, entry in split_map.items()},
        seed=args.seed,
        survives=survives,
    )
    if stats["unplaced"]:
        print(f"no host for {len(stats['unplaced'])} row(s): {stats['unplaced'][:10]}")
        return 1
    model = [plan[str(r["id"])] for r in rows if str(r["id"]).startswith(MODEL_PREFIX)]
    human = [plan[str(r["id"])] for r in rows if str(r["id"]).startswith(HUMAN_PREFIX)]
    out, human_out = args.out, args.human_out
    if args.dry_run is not None:
        out, human_out = args.dry_run / args.out.name, args.dry_run / args.human_out.name
    _write_json(out, model)
    _write_json(human_out, human)
    print(f"wrote {len(model)} + {len(human)} plan rows to {out} and {human_out}")
    if args.dry_run is None:
        return 0

    by_id = {r.id: r for r in corpus}
    dry = dry_run_records(rows, plan, by_id, split_map, by_id)
    (args.dry_run / "synthetic.jsonl").write_text(
        "".join(r.model_dump_json() + "\n" for r in dry), encoding="utf-8", newline="\n"
    )
    report = dry_run_report(
        dry,
        corpus,
        survives=survives,
        lengths=None if args.no_length else (lambda recs: _token_lengths(recs, args.config)),
        stats=stats,
    )
    _write_json(args.dry_run / "report.json", report)
    print(json.dumps({k: report[k] for k in ("rows", "ks_host_plus_payload_chars", "passed")}))
    print(f"survival failures: {len(report['survival_failures'])}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
