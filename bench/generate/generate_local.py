"""Regenerate the synthetic payloads with two local model families (training plan E2, §5.3).

The v0 payloads were the tell (TF-IDF 0.988 against real directive sentences). v1 rewrites the
payload of every model-authored plan row with a local model, under four rules:

1. **Family by server-hash parity.** The planned host's server name, SHA-256, even → Qwen,
   odd → Gemma: two payload families, recorded in `generator_model`.
2. **Register examples per row.** 10 directive sentences drawn, seeded by the row id, from
   real `imperative_benign` rows of DEV servers only, never the row's own host server.
   `build_prompt` refuses a pool holding any other split's server (`SealedTextInPrompt`).
3. **The register lint** (§5.3 item 6) rejects and regenerates: 1-2 sentences (exactly 2 for a
   split two-field payload), 40-160 characters, none of "rather than" / "since" /
   "instead of" / ";", no justification clause, and domains only under `example.invalid`.
4. **No scanner rule text** reaches a prompt: the templates never name the baseline scanner's
   rules (`scanner_rule_names.json`; asserted in the tests).

The client is `bench.ollama` (loopback only, temperature 0, fixed seed, JSON schema, retries,
resumable raw log). A lint failure is asked again with seed + 1 and the reasons; after three
attempts the row is dropped and counted, never patched by hand. Hand-written payloads are not
in the model plan and are never regenerated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from bench.generate.mutate import _gaps, sentence_count
from bench.ollama import OllamaClient, OllamaError, RawLog
from sieve.types import CorpusRecord

PROMPT_DIR = Path(__file__).parent / "prompts" / "v1"
MODELS = {"qwen": "qwen3.8:27b-2.88bpw-text", "gemma": "gemma4:26b-a4b-it-qat"}
REAL_SOURCES = frozenset({"public_repo", "wild_fp"})
REGISTER_EXAMPLES = 10
LINT_ATTEMPTS = 3
LINT_CHARS = (40, 160)
SCHEMA = {
    "type": "object",
    "properties": {"payload": {"type": "string"}},
    "required": ["payload"],
}
_TEMPLATES = {
    "poisoned": "poisoned.md",
    "imperative_benign": "imperative.md",
    "benign": "benign.md",
}
#: A sentence a real author wrote as a directive: the register the examples calibrate.
_CUE = re.compile(
    r"\b(must|always|never|do not|don't|should|only|before|after|ensure|make sure|use |prefer|"
    r"avoid|important|required|call |first)\b",
    re.IGNORECASE,
)
_BANNED = (("rather than", r"\brather than\b"), ("since", r"\bsince\b"),
           ("instead of", r"\binstead of\b"), (";", r";"))  # fmt: skip
_JUSTIFICATION = re.compile(r",\s*(so|because|as|which|given that)\b|\bbecause\b", re.IGNORECASE)
_URL = re.compile(r"https?://([^\s/\"'<>)]+)", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)")
_BARE_DOMAIN = re.compile(
    r"\b((?:[a-z0-9-]+\.)+(?:com|net|org|io|dev|ai|co|app|xyz|info|sh|me|cloud|site|online|"
    r"tech|biz|us|uk|de|ru|cn|gg|ly|so|to|tv|cc|ws|in|eu))\b",
    re.IGNORECASE,
)
RESERVED_DOMAIN = "example.invalid"


RULE_NAMES_FILE = Path(__file__).parent / "scanner_rule_names.json"


def _forbidden_terms() -> frozenset[str]:
    """The pinned scanner's rule names in every spelling, plus the scanner itself (SC-8)."""
    names = json.loads(RULE_NAMES_FILE.read_text(encoding="utf-8"))["rules"]
    terms = {"yara", "mcp-scanner", "mcp_scanner", "signature"}
    for name in names:
        terms |= {name, name.replace("_", " "), name.replace("_", "-")}
    return frozenset(terms)


FORBIDDEN_TERMS = _forbidden_terms()


def names_a_rule(text: str) -> list[str]:
    lower = text.lower()
    return sorted(term for term in FORBIDDEN_TERMS if term in lower)


class RuleNameInPrompt(AssertionError):  # noqa: N818 - a guard, named for what it catches
    """What the generator is told (template and register examples) named a scanner rule."""


class SealedTextInPrompt(AssertionError):  # noqa: N818 - a guard, named for what it catches
    """A register example from a server outside the dev split was about to enter a prompt."""


class Client(Protocol):
    def chat(
        self,
        model: str,
        messages: Sequence[Mapping[str, str]],
        *,
        schema: Mapping[str, Any],
        seed: int,
        options: Mapping[str, Any] | None = None,
    ) -> Any: ...


def family_of(server_name: str) -> str:
    digest = int(hashlib.sha256(server_name.encode("utf-8")).hexdigest(), 16)
    return "qwen" if digest % 2 == 0 else "gemma"


def _sentences(text: str) -> list[str]:
    cuts = [0, *(end for _, end in _gaps(text)), len(text)]
    return [text[a:b].strip() for a, b in zip(cuts, cuts[1:], strict=False) if text[a:b].strip()]


def register_pool(
    records: Sequence[CorpusRecord], split_of: Mapping[str, str]
) -> list[tuple[str, str]]:
    """(server, sentence) for every directive sentence of a real dev `imperative_benign` row."""
    pool = []
    for record in sorted(records, key=lambda r: r.id):
        if record.source not in REAL_SOURCES or record.label != "imperative_benign":
            continue
        if split_of.get(record.server_name, "dev") != "dev":
            continue
        for sentence in _sentences(record.tool.description or ""):
            if 20 <= len(sentence) <= 200 and _CUE.search(sentence) and not names_a_rule(sentence):
                pool.append((record.server_name, sentence))
    return pool


def register_examples(
    pool: Sequence[tuple[str, str]], row_id: str, *, host_server: str, seed: int
) -> list[tuple[str, str]]:
    """10 examples drawn without replacement, seeded by (seed, row id), never from the host."""
    candidates = [entry for entry in pool if entry[0] != host_server]
    digest = hashlib.sha256(f"{seed}:{row_id}".encode()).hexdigest()
    rng = np.random.default_rng(int(digest[:16], 16))
    picks = rng.choice(len(candidates), size=min(REGISTER_EXAMPLES, len(candidates)), replace=False)
    return [candidates[int(i)] for i in picks]


def _field_text(host: CorpusRecord, field: str, property_name: str | None) -> str:
    if field == "description":
        return host.tool.description or "(empty)"
    properties = host.tool.inputSchema.get("properties") or {}
    prop = properties.get(property_name or "") if isinstance(properties, dict) else None
    text = prop.get("description") if isinstance(prop, dict) else None
    return f"(parameter `{property_name}`) {text or '(empty)'}"


def _target(row: Mapping[str, Any]) -> str:
    fields = list(row["target_fields"])
    if fields == ["description"]:
        return "the tool's description"
    if fields == ["inputSchema"]:
        return f"the description of the `{row['property_name']}` parameter"
    return (
        f"split across two places: the first sentence goes in the tool's description, the "
        f"second in the description of the `{row['property_name']}` parameter"
    )


def _position(row: Mapping[str, Any]) -> str:
    where = {"start": "as the first sentence", "middle": "between two existing sentences",
             "end": "as the last sentence"}  # fmt: skip
    parts = [f"{where[p]} of {'the description' if f == 'description' else 'the parameter text'}"
             for f, p in dict(row["position"]).items()]  # fmt: skip
    return "inserted " + " and ".join(parts)


def build_prompt(
    row: Mapping[str, Any],
    hosts: Mapping[str, CorpusRecord],
    pool: Sequence[tuple[str, str]],
    *,
    row_id: str,
    split_of: Mapping[str, str],
    seed: int,
) -> str:
    """The rendered system prompt for one plan row. Refuses a non-dev register example."""
    host = hosts[str(row["host_id"])]
    examples = register_examples(pool, row_id, host_server=host.server_name, seed=seed)
    outside = sorted({s for s, _ in examples if split_of.get(s, "dev") != "dev"})
    if outside:
        raise SealedTextInPrompt(f"{row_id}: register examples from non-dev servers {outside}")
    template = (PROMPT_DIR / _TEMPLATES[str(row["label"])]).read_text(encoding="utf-8")
    fields = list(row["target_fields"])
    host_text = "\n\n".join(_field_text(host, f, row.get("property_name")) for f in fields)
    values = {
        "{{SENTENCES}}": "Exactly two sentences"
        if row.get("split_payload")
        else "One or two sentences",
        "{{SUB_LABELS}}": ", ".join(row.get("sub_labels") or []) or "none",
        "{{DIFFICULTY}}": str(row.get("difficulty") or "not applicable"),
        "{{TARGET}}": _target(row),
        "{{POSITION}}": _position(row),
        "{{HOST_NAME}}": host.tool.name,
        "{{HOST_TEXT}}": host_text,
        "{{EXAMPLES}}": "\n".join(f"- {sentence}" for _, sentence in examples),
    }
    instructions = template
    for slot, value in values.items():
        instructions = instructions.replace(slot, "" if slot == "{{HOST_TEXT}}" else value)
    # SC-8: everything the generator is TOLD is checked. The host text is the row's own data,
    # shown verbatim; a real host may say "signature" or name an attack it defends against.
    named = names_a_rule(instructions)
    if named:
        raise RuleNameInPrompt(f"{row_id}: the prompt's instructions name {named}")
    for slot, value in values.items():
        template = template.replace(slot, value)
    return template


def lint(payload: str, *, split: bool) -> list[str]:
    """Every register rule `payload` breaks (§5.3 item 6); empty when it passes."""
    text = payload.strip()
    errors = []
    if not LINT_CHARS[0] <= len(text) <= LINT_CHARS[1]:
        errors.append(
            f"length: {len(text)} characters, the rule is {LINT_CHARS[0]}-{LINT_CHARS[1]}"
        )
    n = sentence_count(text)
    if split and n != 2:
        errors.append(f"sentences: a split payload needs exactly two sentences, not {n}")
    elif not split and not 1 <= n <= 2:
        errors.append(f"sentences: {n}, the rule is one or two")
    for name, pattern in _BANNED:
        if re.search(pattern, text, re.IGNORECASE):
            errors.append(f"banned phrase: {name!r}")
    if _JUSTIFICATION.search(text):
        errors.append("justification clause: do not explain why")
    hosts = [m.group(1) for m in _URL.finditer(text)] + [m.group(1) for m in _EMAIL.finditer(text)]
    hosts += [m.group(1) for m in _BARE_DOMAIN.finditer(_URL.sub(" ", text))]
    for host in hosts:
        name = host.lower().rstrip(".")
        if name != RESERVED_DOMAIN and not name.endswith("." + RESERVED_DOMAIN):
            errors.append(f"domain: {host} is not under {RESERVED_DOMAIN}")
    return errors


def generate_row(
    client: Client,
    row: Mapping[str, Any],
    row_id: str,
    hosts: Mapping[str, CorpusRecord],
    pool: Sequence[tuple[str, str]],
    split_of: Mapping[str, str],
    *,
    log: RawLog,
    seed: int,
) -> dict[str, Any]:
    """One row: generate, lint, regenerate with the reasons; logged, and skipped once logged."""
    host = hosts[str(row["host_id"])]
    family = family_of(host.server_name)
    model = MODELS[family]
    done = log.get(model, row_id)
    if done is not None:
        return done
    prompt = build_prompt(row, hosts, pool, row_id=row_id, split_of=split_of, seed=seed)
    base = [{"role": "system", "content": prompt}, {"role": "user", "content": "Write the sample."}]
    messages = list(base)
    tried: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    for attempt in range(LINT_ATTEMPTS):
        try:
            reply = client.chat(model, messages, schema=SCHEMA, seed=seed + attempt)
        except OllamaError as error:
            tried.append({"seed": seed + attempt, "error": str(error)})
            result = {"status": "dropped", "reason": f"no reply: {error}"}
            break
        payload = str(reply.parsed["payload"]).strip()
        errors = lint(payload, split=bool(row.get("split_payload")))
        tried.append({"seed": seed + attempt, "payload": payload, "errors": errors,
                      "eval_count": reply.eval_count})  # fmt: skip
        if not errors:
            result = {"status": "accepted", "payload": payload}
            break
        feedback = f"That was rejected: {'; '.join(errors)}. Write it again and follow every rule."
        messages = [*base, {"role": "assistant", "content": payload},
                    {"role": "user", "content": feedback}]  # fmt: skip
    if result is None:
        result = {"status": "dropped", "reason": "; ".join(tried[-1]["errors"])}
    result.update(
        {
            "model": model,
            "family": family,
            "attempts": tried,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        }
    )
    log.put(model, row_id, result)
    return result


def write_payloads(out: Path, results: Mapping[str, Mapping[str, Any]], *, n: int) -> None:
    """`mutate --generator file` input: one line per plan index; a dropped row keeps its index
    with `payload: null`, so every other id stays where the frozen split map expects it."""
    lines = []
    for i in range(n):
        result = results[f"sb-syn-{i:04d}"]
        accepted = result["status"] == "accepted"
        lines.append(
            {
                "plan_index": i,
                "payload": result["payload"] if accepted else None,
                "generator_model": result["model"],
                "dropped": None if accepted else result.get("reason", "dropped"),
            }
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
        newline="\n",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.generate.generate_local")
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--plan", type=Path, default=Path("bench/data/synthetic/plan_v1.json"))
    parser.add_argument("--corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--split-map", type=Path, default=Path("bench/data/split_map.json"))
    parser.add_argument("--out", type=Path, default=Path("bench/data/synthetic/payloads_v1.jsonl"))
    parser.add_argument(
        "--raw-log", type=Path, default=Path("bench/data/synthetic/raw/generate_v1.jsonl")
    )
    parser.add_argument("--seed", type=int, default=0)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bench", type=int, metavar="N", help="time the first N rows")
    mode.add_argument("--all", action="store_true", help="every model-authored plan row")
    ns = parser.parse_args(argv)
    client = OllamaClient(base_url=ns.url)  # refuses a non-loopback URL before reading anything

    plan = json.loads(ns.plan.read_text(encoding="utf-8"))
    records = [
        CorpusRecord.model_validate_json(line)
        for line in ns.corpus.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    hosts = {r.id: r for r in records}
    servers = json.loads(ns.split_map.read_text(encoding="utf-8"))["servers"]
    split_of = {name: str(entry["split"]) for name, entry in servers.items()}
    pool = register_pool(records, split_of)
    log = RawLog(ns.raw_log)
    todo = range(ns.bench if ns.bench else len(plan))
    results: dict[str, dict[str, Any]] = {}
    started, fresh = time.perf_counter(), 0
    for i in todo:
        row_id = f"sb-syn-{i:04d}"
        model = MODELS[family_of(hosts[plan[i]["host_id"]].server_name)]
        fresh += log.get(model, row_id) is None
        results[row_id] = generate_row(client, plan[i], row_id, hosts, pool, split_of, log=log,
                                       seed=ns.seed)  # fmt: skip
        status = results[row_id]["status"]
        print(f"{row_id} {results[row_id]['family']:5} {status}", flush=True)
    elapsed = time.perf_counter() - started
    dropped = sorted(k for k, v in results.items() if v["status"] != "accepted")
    summary: dict[str, Any] = {
        "rows": len(results),
        "dropped": len(dropped),
        "seconds": round(elapsed, 1),
    }
    if fresh:
        per_row = elapsed / fresh
        summary["rows_per_second"] = round(1 / per_row, 4)
        summary["projected_hours_all"] = round(per_row * len(plan) / 3600, 2)
    print(json.dumps(summary))
    if ns.all:
        write_payloads(ns.out, results, n=len(plan))
        print(f"wrote {ns.out} ({len(plan) - len(dropped)} accepted, {len(dropped)} dropped)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
