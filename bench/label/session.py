"""The labelling session: a pure class over a JSONL corpus file.

Blind mode (PRD §9.6 item 2) hides more than the label. It hides the tier and the
sub-labels too, because those *are* the generator's intent and seeing them anchors the
human exactly as seeing the label would. On a synthetic row the blind primary pass is
what produces the gold label, so the generator's intent is copied to a sidecar
(`generator_intent.jsonl`) *before* the human's label overwrites it; Task 1.7 reads that
sidecar to publish blind-vs-generator agreement.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sieve.types import Concealment, CorpusRecord, Difficulty, Label, SubLabel

DEFAULT_INTENT_PATH = Path("bench/data/generator_intent.jsonl")
RATIONALE_REQUIRED: frozenset[Label] = frozenset({"imperative_benign", "poisoned"})

#: Fields blind mode must never render: the label, and everything that leaks it.
BLIND_HIDDEN = (
    "label",
    "second_label",
    "sub_labels",
    "difficulty",
    "concealment",
    "label_rationale",
    "generator_model",
    "mutation_of",
    "payload_fields",
)


class IncompleteLabel(Exception):  # noqa: N818 (name fixed by the lane's published interface)
    """Raised by `save()` when a directive-class row has no rationale."""

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        super().__init__(
            "a rationale is required for imperative_benign and poisoned rows; missing on: "
            + ", ".join(ids)
        )


class LabelSession:
    """Stateful cursor over a JSONL corpus, with atomic saves."""

    def __init__(
        self,
        path: Path,
        labeller: str,
        *,
        blind: bool = False,
        second: bool = False,
        intent_path: Path = DEFAULT_INTENT_PATH,
    ) -> None:
        self.path = Path(path)
        self.labeller = labeller
        self.blind = blind
        self.second = second
        self.intent_path = Path(intent_path)
        self.records: list[CorpusRecord] = [
            CorpusRecord.model_validate_json(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not self.records:
            raise ValueError(f"{self.path} holds no records")
        self.index = 0
        self._touched: set[str] = set()
        self._intent_recorded: set[str] = set()

    # -- navigation ---------------------------------------------------------

    def current(self) -> CorpusRecord:
        return self.records[self.index]

    def next(self) -> CorpusRecord:
        self.index = min(self.index + 1, len(self.records) - 1)
        return self.current()

    def prev(self) -> CorpusRecord:
        self.index = max(self.index - 1, 0)
        return self.current()

    def progress(self) -> tuple[int, int]:
        return self.index + 1, len(self.records)

    # -- editing ------------------------------------------------------------

    def set_label(self, label: Label) -> None:
        record = self.current()
        if self.second:
            record.second_label = label
            self._touched.add(record.id)
            return
        self._record_generator_intent(record)
        record.label = label
        record.labeller = self.labeller
        record.labelled_at = datetime.now(tz=UTC)
        self._touched.add(record.id)

    def toggle_sub_label(self, sub_label: SubLabel) -> None:
        record = self.current()
        if sub_label in record.sub_labels:
            record.sub_labels = [s for s in record.sub_labels if s != sub_label]
        else:
            record.sub_labels = [*record.sub_labels, sub_label]
        self._touched.add(record.id)

    def set_difficulty(self, difficulty: Difficulty | None) -> None:
        self.current().difficulty = difficulty
        self._touched.add(self.current().id)

    def set_concealment(self, concealment: Concealment) -> None:
        self.current().concealment = concealment
        self._touched.add(self.current().id)

    def set_rationale(self, text: str) -> None:
        self.current().label_rationale = text or None
        self._touched.add(self.current().id)

    # -- persistence --------------------------------------------------------

    def save(self) -> None:
        """Validate, then replace the corpus file atomically (temp file + `os.replace`)."""
        self._validate()
        payload = "".join(r.model_dump_json() + "\n" for r in self.records)
        directory = self.path.parent
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 (closed explicitly below)
            "w",
            encoding="utf-8",
            newline="\n",
            dir=directory,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            delete=False,
        )
        temp = Path(handle.name)
        try:
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def _validate(self) -> None:
        if self.second:
            return
        missing = [
            r.id
            for r in self.records
            if r.id in self._touched
            and r.label in RATIONALE_REQUIRED
            and not (r.label_rationale or "").strip()
        ]
        if missing:
            raise IncompleteLabel(missing)

    def _record_generator_intent(self, record: CorpusRecord) -> None:
        """Copy the generator's intent to the sidecar before the human's label lands."""
        if not self.blind or self.second:
            return
        if record.source != "synthetic_mutation" or record.id in self._intent_recorded:
            return
        entry = {
            "id": record.id,
            "intended_label": record.label,
            "intended_sub_labels": list(record.sub_labels),
            "intended_difficulty": record.difficulty,
            "generator_model": record.generator_model,
        }
        self.intent_path.parent.mkdir(parents=True, exist_ok=True)
        with self.intent_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry) + "\n")
        self._intent_recorded.add(record.id)

    # -- rendering ----------------------------------------------------------

    def render(self) -> str:
        """The read-only pane the human labels from. Blind mode omits every anchor."""
        record = self.current()
        position, total = self.progress()
        tool = record.tool
        lines = [
            f"[{position}/{total}] {record.id}   {record.server_name}/{tool.name}",
            f"source: {record.source}   capture: {record.capture_method}"
            f"   fidelity: {record.schema_fidelity}",
            "",
            f"name: {tool.name}",
            f"title: {tool.title or ''}",
            f"description: {tool.description or ''}",
            f"inputSchema: {json.dumps(tool.inputSchema, indent=2)}",
            f"outputSchema: {json.dumps(tool.outputSchema) if tool.outputSchema else ''}",
            f"annotations: {json.dumps(tool.annotations) if tool.annotations else ''}",
            f"siblings: {', '.join(record.sibling_tool_names)}",
            f"server_instructions: {record.server_instructions or ''}",
        ]
        if not self.blind:
            lines += [
                "",
                f"label: {record.label}",
                f"second_label: {record.second_label}",
                f"sub_labels: {', '.join(record.sub_labels)}",
                f"difficulty: {record.difficulty}",
                f"concealment: {record.concealment}",
                f"payload_fields: {', '.join(record.payload_fields)}",
                f"mutation_of: {record.mutation_of}",
                f"generator_model: {record.generator_model}",
                f"label_rationale: {record.label_rationale or ''}",
            ]
        return "\n".join(lines)
