"""Mutation-based synthesis (PRD §9.5).

Three things about this module matter more than the code:

1. The label `mutate` writes is the **generator's intent**, not gold. The human overwrites it
   in a blind pass (`bench.label.session`), which is why `label_rationale` starts empty and
   `adjudicated` starts False.
2. The system prompt never contains a detection rule or a rule name (PRD SC-8). A generator
   shown the rules writes payloads that trip the rules, and any false-positive reduction
   measured against those rules is then circular. `tests/bench/test_generate_prompt.py` is
   the gate.
3. `PayloadSpec.label` includes `"benign"` on purpose: §9.4 needs ≈ 40 synthetic *benign*
   paraphrases, without which `source` is collinear with `label` and the leakage probe
   cannot mean anything.
"""

from __future__ import annotations

import argparse
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from bench.generate import conceal
from sieve.types import (
    Concealment,
    CorpusRecord,
    Difficulty,
    EvidenceField,
    Label,
    SubLabel,
)

PROMPT_DIR = Path(__file__).parent / "prompts"
HUMAN_PAYLOADS = Path("bench/generate/human_payloads.jsonl")
DEFAULT_MODEL = "claude-sonnet-5"
MAX_PAYLOAD_TOKENS = 300


class NoHumanPayload(Exception):  # noqa: N818 (name fixed by the lane's published interface)
    """The hand-authored payload file has no unused entry matching this spec.

    Crashing is correct: PRD §9.4 requires ≥ 25% of poisoned payloads to be hand-authored,
    and a silent fall back to the model would quietly break that guarantee.
    """


class PayloadSpec(BaseModel):
    label: Label
    sub_labels: list[SubLabel]
    difficulty: Difficulty | None
    target_fields: list[EvidenceField]
    property_name: str | None
    concealment: Concealment = "none"
    host_name: str | None = None


@runtime_checkable
class Generator(Protocol):
    model_id: str

    def generate(self, spec: PayloadSpec, host_description: str) -> str: ...


def _template(label: Label) -> str:
    name = "imperative.md" if label == "imperative_benign" else "payload.md"
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def render_system_prompt(spec: PayloadSpec, wild_examples: list[str]) -> str:
    """Render the generation prompt. Never contains a detection rule (PRD SC-8)."""
    substitutions = {
        "{{LABEL}}": spec.label,
        "{{SUB_LABELS}}": ", ".join(spec.sub_labels) or "none",
        "{{DIFFICULTY}}": spec.difficulty or "not applicable",
        "{{TARGET_FIELDS}}": ", ".join(spec.target_fields),
        "{{PROPERTY_NAME}}": spec.property_name or "not applicable",
        "{{HOST_NAME}}": spec.host_name or "not given",
        "{{WILD_EXAMPLES}}": "\n".join(f"- {e}" for e in wild_examples),
    }
    text = _template(spec.label)
    for token, value in substitutions.items():
        text = text.replace(token, value)
    return text


class AnthropicGenerator:
    """Generates one payload per call. Costs money; every call is a real API request."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        client: Any | None = None,
        wild_examples: list[str] | None = None,
    ) -> None:
        self.model_id = model
        self.wild_examples = wild_examples or []
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def generate(self, spec: PayloadSpec, host_description: str) -> str:
        system = render_system_prompt(spec, self.wild_examples)
        system = system.replace("{{HOST_DESCRIPTION}}", host_description)
        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=MAX_PAYLOAD_TOKENS,
            system=[
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": "Write the sample."}],
        )
        return "".join(b.text for b in response.content if b.type == "text").strip()


class HumanGenerator:
    """Pops the next unused hand-authored payload matching the spec. Never falls back."""

    model_id = "human"

    def __init__(self, path: Path = HUMAN_PAYLOADS) -> None:
        self.path = Path(path)
        self.payloads: list[dict[str, Any]] = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def generate(self, spec: PayloadSpec, host_description: str) -> str:
        for index, entry in enumerate(self.payloads):
            if set(entry.get("sub_labels", [])) != set(spec.sub_labels):
                continue
            if entry.get("difficulty") != spec.difficulty:
                continue
            return str(self.payloads.pop(index)["payload"])
        raise NoHumanPayload(
            f"no hand-authored payload left for sub_labels={spec.sub_labels} "
            f"difficulty={spec.difficulty} in {self.path}"
        )


def _append(existing: str | None, payload: str) -> str:
    return f"{existing} {payload}".strip() if existing else payload


def _inject(record: CorpusRecord, field: EvidenceField, payload: str, property_name: str) -> None:
    if field == "description":
        record.tool.description = _append(record.tool.description, payload)
    elif field == "inputSchema":
        properties = record.tool.inputSchema.setdefault("properties", {})
        prop = properties.setdefault(property_name, {"type": "string"})
        prop["description"] = _append(prop.get("description"), payload)
    elif field == "annotations":
        annotations = record.tool.annotations or {}
        annotations["title"] = _append(annotations.get("title"), payload)
        record.tool.annotations = annotations
    else:
        raise ValueError(f"cannot inject a payload into {field}")


def mutate(host: CorpusRecord, spec: PayloadSpec, gen: Generator, *, new_id: str) -> CorpusRecord:
    """Insert a generated payload into a copy of `host` and return the synthetic record."""
    record = copy.deepcopy(host)
    payload = conceal.apply(gen.generate(spec, host.tool.description or ""), spec.concealment)
    property_name = spec.property_name or "input"
    for field in spec.target_fields:
        _inject(record, field, payload, property_name)
    record.id = new_id
    record.source = "synthetic_mutation"
    record.capture_method = "authored"
    record.mutation_of = host.id
    record.payload_fields = list(spec.target_fields)
    record.concealment = spec.concealment
    record.generator_model = gen.model_id
    record.label = spec.label
    record.sub_labels = list(spec.sub_labels)
    record.difficulty = spec.difficulty
    record.labeller = "generator"
    record.labelled_at = datetime.now(tz=UTC)
    record.label_rationale = None
    record.second_label = None
    record.adjudicated = False
    record.split = None
    record.fold = None
    return record


def _load(path: Path) -> list[CorpusRecord]:
    return [
        CorpusRecord.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.generate.mutate",
        description="Mutate host records into synthetic samples from a plan of PayloadSpec rows.",
    )
    parser.add_argument("--corpus", type=Path, required=True, help="JSONL of host records")
    parser.add_argument("--plan", type=Path, required=True, help="JSON list of plan rows")
    parser.add_argument("--out", type=Path, required=True, help="JSONL to write mutations to")
    parser.add_argument(
        "--generator",
        choices=("human", "anthropic"),
        default="human",
        help="hand-authored payloads, or the model (an API key and real spend)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--id-prefix", default="sb-syn-")
    args = parser.parse_args(argv)

    records = {r.id: r for r in _load(args.corpus)}
    wild = [
        r.tool.description
        for r in records.values()
        if r.label == "imperative_benign" and r.tool.description
    ][:10]
    gen: Generator = (
        HumanGenerator()
        if args.generator == "human"
        else AnthropicGenerator(args.model, wild_examples=wild)
    )
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    out: list[CorpusRecord] = []
    for index, row in enumerate(plan):
        host = records[row.pop("host_id")]
        spec = PayloadSpec(**row, host_name=host.tool.name)
        out.append(mutate(host, spec, gen, new_id=f"{args.id_prefix}{index:04d}"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "".join(r.model_dump_json() + "\n" for r in out), encoding="utf-8", newline="\n"
    )
    print(f"wrote {len(out)} mutations to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
