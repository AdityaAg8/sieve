"""The labelling session: a pure class over a JSONL corpus file.

Blind mode (PRD §9.6 item 2) hides more than the label. It hides the tier and the
sub-labels too, because those *are* the generator's intent and seeing them anchors the
human exactly as seeing the label would. On a synthetic row the blind primary pass is
what produces the gold label, so the generator's intent is copied to a sidecar
(`generator_intent.jsonl`) *before* the human's label overwrites it; Task 1.7 reads that
sidecar to publish blind-vs-generator agreement.

Blind also means (training plan F4, findings F39-F42): no id, source or capture line in the
pane; rows in a seeded permutation that is never written back; a sidecar that keeps the FIRST
intent recorded for an id, so a resume or a retest cannot overwrite it with a human label; and
on the first blind touch of a row, the generator's sub-labels, tier and rationale are cleared so
every toggle acts on the human's own selections, which the pane then shows.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from sieve.types import Concealment, CorpusRecord, Difficulty, Label, SubLabel

DEFAULT_INTENT_PATH = Path("bench/data/generator_intent.jsonl")
DEFAULT_ORDER_SEED = 20261002
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
        ids: Sequence[str] | None = None,
        order_seed: int = DEFAULT_ORDER_SEED,
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
        self.order = self._presentation_order(ids, order_seed)
        self.index = 0
        self._touched: set[str] = set()
        self._claimed: set[str] = set()
        self._intent_recorded: set[str] = self._intent_ids()

    def _presentation_order(self, ids: Sequence[str] | None, seed: int) -> list[int]:
        """Indices into `records` in the order the human sees them; never saved anywhere."""
        position = {r.id: i for i, r in enumerate(self.records)}
        if ids is None:
            order = list(range(len(self.records)))
        else:
            unknown = [i for i in ids if i not in position]
            if unknown:
                raise ValueError(f"ids not in {self.path}: {unknown[:5]}")
            order = sorted({position[i] for i in ids})
        if self.blind:
            permuted = np.random.default_rng(seed).permutation(len(order))
            order = [order[int(k)] for k in permuted]
        return order

    def _intent_ids(self) -> set[str]:
        if not self.intent_path.exists():
            return set()
        lines = self.intent_path.read_text(encoding="utf-8").splitlines()
        return {json.loads(line)["id"] for line in lines if line.strip()}

    # -- navigation ---------------------------------------------------------

    def current(self) -> CorpusRecord:
        return self.records[self.order[self.index]]

    def next(self) -> CorpusRecord:
        self.index = min(self.index + 1, len(self.order) - 1)
        return self.current()

    def prev(self) -> CorpusRecord:
        self.index = max(self.index - 1, 0)
        return self.current()

    def progress(self) -> tuple[int, int]:
        return self.index + 1, len(self.order)

    # -- editing ------------------------------------------------------------

    def _claim(self, record: CorpusRecord) -> None:
        """First blind touch of a row: keep the generator's intent, then hand the row over.

        A row the human already labelled (an earlier session wrote their name on it) is theirs
        and is left alone, so resuming never wipes their own sub-labels or rationale.
        """
        if not self.blind or self.second or record.id in self._claimed:
            return
        self._claimed.add(record.id)
        if record.labeller == self.labeller:
            return
        self._record_generator_intent(record)
        record.sub_labels = []
        record.difficulty = None
        record.label_rationale = None

    def set_label(self, label: Label) -> None:
        record = self.current()
        if self.second:
            record.second_label = label
            self._touched.add(record.id)
            return
        self._claim(record)
        self._record_generator_intent(record)
        record.label = label
        record.labeller = self.labeller
        record.labelled_at = datetime.now(tz=UTC)
        self._touched.add(record.id)

    # The second annotator writes `second_label` and nothing else: every other field belongs to
    # the primary pass, so the edits below are no-ops in second mode.

    def toggle_sub_label(self, sub_label: SubLabel) -> None:
        if self.second:
            return
        record = self.current()
        self._claim(record)
        if sub_label in record.sub_labels:
            record.sub_labels = [s for s in record.sub_labels if s != sub_label]
        else:
            record.sub_labels = [*record.sub_labels, sub_label]
        self._touched.add(record.id)

    def set_difficulty(self, difficulty: Difficulty | None) -> None:
        if self.second:
            return
        self._claim(self.current())
        self.current().difficulty = difficulty
        self._touched.add(self.current().id)

    def set_concealment(self, concealment: Concealment) -> None:
        if self.second:
            return
        self._claim(self.current())
        self.current().concealment = concealment
        self._touched.add(self.current().id)

    def set_rationale(self, text: str) -> None:
        if self.second:
            return
        self._claim(self.current())
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
        """Copy the generator's intent to the sidecar before the human's label lands.

        First wins: an id already in the sidecar (from this or any earlier session) is skipped.
        """
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
        if self.blind:
            header = [f"[{position}/{total}]   {record.server_name}/{tool.name}", ""]
        else:
            header = [
                f"[{position}/{total}] {record.id}   {record.server_name}/{tool.name}",
                f"source: {record.source}   capture: {record.capture_method}"
                f"   fidelity: {record.schema_fidelity}",
                "",
            ]
        lines = [
            *header,
            f"name: {tool.name}",
            f"title: {tool.title or ''}",
            f"description: {tool.description or ''}",
            f"inputSchema: {json.dumps(tool.inputSchema, indent=2)}",
            f"outputSchema: {json.dumps(tool.outputSchema) if tool.outputSchema else ''}",
            f"annotations: {json.dumps(tool.annotations) if tool.annotations else ''}",
            f"siblings: {', '.join(record.sibling_tool_names)}",
            f"server_instructions: {record.server_instructions or ''}",
        ]
        if self.blind:
            lines += self._own_selections(record)
        else:
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

    def _own_selections(self, record: CorpusRecord) -> list[str]:
        """What the human has chosen on this row, and nothing they did not choose."""
        if self.second:
            return ["", f"your label = {record.second_label}"] if record.second_label else []
        labelled = record.labeller == self.labeller
        if record.id not in self._claimed and not labelled:
            return []
        out = [""]
        if labelled:
            out.append(f"your label = {record.label}")
        out += [
            f"your sub-labels = {', '.join(record.sub_labels) or '-'}",
            f"your tier = {record.difficulty or '-'}",
            f"your rationale = {record.label_rationale or '-'}",
        ]
        return out
