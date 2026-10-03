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
import re
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
    #: Training plan E1 (§5.3 item 2): per field, the host sentence the payload goes BEFORE
    #: (0 = first, the field's sentence count = after the last). None appends, as v0 did.
    insert_at: dict[EvidenceField, int] | None = None
    #: §5.3 item 3: a two-field payload is split across its fields, never copied into both.
    split_payload: bool = False


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


class FileGenerator:
    """Replays payloads a model wrote outside this process, one per plan row, in plan order.

    One file is one generator: every line must name the same `generator_model`, which becomes
    each record's provenance. `plan_index` has to count 0, 1, 2 ... so a file that drifts out
    of step with its plan fails loudly rather than pairing a payload with the wrong host.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.entries: list[dict[str, Any]] = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        models = sorted({str(e["generator_model"]) for e in self.entries})
        if len(models) != 1:
            raise ValueError(f"{self.path}: expected one generator_model, found {models}")
        self.model_id = models[0]
        self._next = 0

    def generate(self, spec: PayloadSpec, host_description: str) -> str:
        if self._next >= len(self.entries):
            raise ValueError(f"{self.path} has no payload for plan row {self._next}")
        entry = self.entries[self._next]
        if entry["plan_index"] != self._next:
            raise ValueError(
                f"{self.path}: plan_index {entry['plan_index']} where {self._next} was expected"
            )
        self._next += 1
        return str(entry["payload"])


#: Characters a sentence can end on, so the payload can simply follow after a space.
_SENTENCE_END = frozenset(".!?:;。！？：；")
#: Closing marks that may follow a sentence's end: `say "done."` has already ended.
_CLOSERS = "\"')]}”’»"


def _append(existing: str | None, payload: str) -> str:
    """`payload` as the next sentence of `existing`, or its next paragraph.

    A third of real hosts end without a full stop, and gluing a sentence straight on ("Scroll
    the page Do not ...") leaves a seam only synthetic rows have. Every poisoned row is
    synthetic, so the model would learn to spot the mutation instead of the payload. A host
    that ends on a line break (a code fence, a list) takes the payload as a new paragraph.
    """
    if not existing or not existing.strip():
        return payload
    base = existing.rstrip()
    if "\n" in existing[len(base) :]:
        joiner = "\n\n"
    elif base.rstrip(_CLOSERS)[-1:] in _SENTENCE_END:
        joiner = " "
    else:
        joiner = ". "
    return f"{base}{joiner}{payload}".strip()


#: Where a sentence ends inside running text. ":" and ";" do not end one here: a boundary
#: on them would put a payload between a label and its list.
_FULL_STOP = frozenset(".!?。！？")
_WHITESPACE = re.compile(r"\s+")


def _gaps(text: str) -> list[tuple[int, int]]:
    """The whitespace runs between sentences: after a full stop (closing quotes allowed), or
    any run holding a line break. Runs at either end of the text are not between sentences."""
    gaps = []
    for match in _WHITESPACE.finditer(text):
        start, end = match.span()
        if start == 0 or end == len(text):
            continue
        if "\n" in match.group() or text[:start].rstrip(_CLOSERS)[-1:] in _FULL_STOP:
            gaps.append((start, end))
    return gaps


def sentence_count(text: str | None) -> int:
    """How many sentences `insert_sentence` sees in `text` (0 for an empty field)."""
    if not text or not text.strip():
        return 0
    return len(_gaps(text)) + 1


def _terminate(payload: str) -> str:
    stripped = payload.rstrip()
    return stripped if stripped.rstrip(_CLOSERS)[-1:] in _FULL_STOP else f"{stripped}."


def insert_sentence(
    existing: str | None, payload: str, index: int, *, terminate: bool = True
) -> str:
    """`payload` as a sentence of its own, before sentence `index` of `existing`.

    Index 0 opens the field, `sentence_count(existing)` (or more) closes it the way `_append`
    always did, and anything between lands on that sentence boundary, reusing its whitespace
    so a line-break boundary stays one. `terminate=False` is for an already-concealed payload,
    whose full stop is inside the concealment.
    """
    text = _terminate(payload) if terminate else payload
    if not existing or not existing.strip():
        return text
    gaps = _gaps(existing)
    if index <= 0:
        return f"{text} {existing.lstrip()}"
    if index > len(gaps):
        return _append(existing, text)
    start, end = gaps[index - 1]
    sep = existing[start:end]
    return f"{existing[:start]}{sep}{text}{sep}{existing[end:]}"


def split_payload(payload: str) -> tuple[str, str]:
    """Two halves of one payload, for the description + inputSchema site (§5.3 item 3).

    Between sentences when there are two or more, otherwise after the comma nearest the middle,
    otherwise at the space nearest the middle.
    """
    text = payload.strip()
    gaps = _gaps(text)
    if gaps:
        start, end = gaps[(len(gaps) + 1) // 2 - 1]
        return text[:start].strip(), text[end:].strip()
    middle = len(text) / 2
    commas = [m.end() for m in re.finditer(r",\s", text)]
    spaces = [m.start() for m in re.finditer(r"\s", text)]
    cuts = commas or spaces
    if not cuts:
        raise ValueError(f"cannot split a one-word payload across two fields: {payload!r}")
    cut = min(cuts, key=lambda c: (abs(c - middle), c))
    return text[:cut].strip(), text[cut:].strip()


def _place(existing: str | None, payload: str, index: int | None) -> str:
    if index is None:
        return _append(existing, payload)
    return insert_sentence(existing, payload, index, terminate=False)


def _inject(
    record: CorpusRecord,
    field: EvidenceField,
    payload: str,
    property_name: str,
    index: int | None = None,
) -> None:
    if field == "description":
        record.tool.description = _place(record.tool.description, payload, index)
    elif field == "inputSchema":
        properties = record.tool.inputSchema.setdefault("properties", {})
        prop = properties.setdefault(property_name, {"type": "string"})
        prop["description"] = _place(prop.get("description"), payload, index)
    elif field == "annotations":
        annotations = record.tool.annotations or {}
        annotations["title"] = _place(annotations.get("title"), payload, index)
        record.tool.annotations = annotations
    else:
        raise ValueError(f"cannot inject a payload into {field}")


def _pieces(raw: str, spec: PayloadSpec) -> list[str]:
    """The (unconcealed) text each target field receives, in `target_fields` order."""
    if spec.insert_at is None:
        return [raw] * len(spec.target_fields)  # v0: appended as written, to every field
    text = _terminate(raw)
    if spec.split_payload:
        if len(spec.target_fields) != 2:
            raise ValueError("a split payload needs exactly two target fields")
        return list(split_payload(text))
    return [text] * len(spec.target_fields)


def mutate(host: CorpusRecord, spec: PayloadSpec, gen: Generator, *, new_id: str) -> CorpusRecord:
    """Insert a generated payload into a copy of `host` and return the synthetic record."""
    record = copy.deepcopy(host)
    raw = gen.generate(spec, host.tool.description or "")
    property_name = spec.property_name or "input"
    for field, piece in zip(spec.target_fields, _pieces(raw, spec), strict=True):
        index = None if spec.insert_at is None else spec.insert_at.get(field)
        _inject(record, field, conceal.apply(piece, spec.concealment), property_name, index)
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
        choices=("human", "anthropic", "file"),
        default="human",
        help="hand-authored payloads, the model (an API key and real spend), or a payload file",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--payloads",
        type=Path,
        default=None,
        help="with --generator file: JSONL of {plan_index, payload, generator_model}",
    )
    parser.add_argument("--id-prefix", default="sb-syn-")
    args = parser.parse_args(argv)
    if args.generator == "file" and args.payloads is None:
        parser.error("--generator file needs --payloads")

    records = {r.id: r for r in _load(args.corpus)}
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    wild = [
        r.tool.description
        for r in records.values()
        if r.label == "imperative_benign" and r.tool.description
    ][:10]
    gen: Generator
    if args.generator == "human":
        gen = HumanGenerator()
    elif args.generator == "anthropic":
        gen = AnthropicGenerator(args.model, wild_examples=wild)
    else:
        gen = FileGenerator(args.payloads)
        if len(gen.entries) != len(plan):
            raise SystemExit(
                f"{args.payloads} holds {len(gen.entries)} payloads for {len(plan)} plan rows"
            )
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
