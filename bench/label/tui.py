"""Keyboard-driven labelling TUI. Thin: every piece of state lives in `LabelSession`.

Key-map note. The brief binds `e/x/s/p/c/k/m` to the seven sub-labels *and* `n/p` to
navigation, which collides on `p`. The seven-letter sub-label set is a mnemonic block, so
`p` stays `privilege_escalation` and "previous" moves to `shift+p` / `left`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Static

from bench.label.sample import dev_audit_ids
from bench.label.session import (
    DEFAULT_INTENT_PATH,
    DEFAULT_ORDER_SEED,
    IncompleteLabel,
    LabelSession,
)
from sieve.types import Concealment, CorpusRecord, Difficulty, Label, SubLabel

LABEL_KEYS: dict[str, Label] = {
    "1": "benign",
    "2": "imperative_benign",
    "3": "poisoned",
}

SUB_LABEL_KEYS: dict[str, SubLabel] = {
    "e": "exfiltration",
    "x": "instruction_override",
    "s": "tool_shadowing",
    "p": "privilege_escalation",
    "c": "concealment",
    "k": "secret_solicitation",
    "m": "command_execution",
}

DIFFICULTY_CYCLE: tuple[Difficulty | None, ...] = (None, "easy", "medium", "hard")
CONCEALMENT_CYCLE: tuple[Concealment, ...] = (
    "none",
    "html_comment",
    "zero_width",
    "bidi",
    "whitespace",
)


class LabelApp(App[None]):
    """Renders `session.render()` and maps keys onto session methods. No state of its own."""

    CSS = "#pane { height: 1fr; overflow: auto; } #status { height: auto; }"

    BINDINGS = [
        *[(key, f"set_label('{lab}')", lab) for key, lab in LABEL_KEYS.items()],
        *[(key, f"toggle_sub('{sub}')", sub) for key, sub in SUB_LABEL_KEYS.items()],
        ("d", "cycle_difficulty", "tier"),
        ("h", "cycle_concealment", "concealment"),
        ("r", "edit_rationale", "rationale"),
        ("n", "go_next", "next"),
        ("P,left", "go_prev", "previous"),
        ("w", "save", "save"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, session: LabelSession) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static(id="pane")
            yield Static(id="status")
            yield Input(placeholder="rationale", id="rationale")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#rationale", Input).display = False
        self._refresh()

    def _refresh(self, status: str = "") -> None:
        self.query_one("#pane", Static).update(self.session.render())
        position, total = self.session.progress()
        self.query_one("#status", Static).update(f"{position}/{total}  {status}")

    # -- actions ------------------------------------------------------------

    def action_set_label(self, label: Label) -> None:
        self.session.set_label(label)
        self._refresh(f"label = {label}")

    def action_toggle_sub(self, sub_label: SubLabel) -> None:
        self.session.toggle_sub_label(sub_label)
        self._refresh(f"toggled {sub_label}")

    def action_cycle_difficulty(self) -> None:
        current = self.session.current().difficulty
        nxt = DIFFICULTY_CYCLE[(DIFFICULTY_CYCLE.index(current) + 1) % len(DIFFICULTY_CYCLE)]
        self.session.set_difficulty(nxt)
        self._refresh(f"tier = {nxt}")

    def action_cycle_concealment(self) -> None:
        current = self.session.current().concealment
        nxt = CONCEALMENT_CYCLE[(CONCEALMENT_CYCLE.index(current) + 1) % len(CONCEALMENT_CYCLE)]
        self.session.set_concealment(nxt)
        self._refresh(f"concealment = {nxt}")

    def action_edit_rationale(self) -> None:
        box = self.query_one("#rationale", Input)
        box.display = True
        box.value = self.session.current().label_rationale or ""
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.session.set_rationale(event.value)
        event.input.display = False
        self.set_focus(None)
        self._refresh("rationale saved")

    def action_go_next(self) -> None:
        self.session.next()
        self._refresh()

    def action_go_prev(self) -> None:
        self.session.prev()
        self._refresh()

    def action_save(self) -> None:
        try:
            self.session.save()
        except IncompleteLabel as exc:
            self._refresh(str(exc))
        else:
            self._refresh("saved")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m bench.label.tui",
        description="Label a JSONL corpus by keyboard. Blind mode hides the generator's intent.",
    )
    parser.add_argument("corpus", type=Path, help="JSONL corpus file to label in place")
    parser.add_argument("--labeller", default="aditya", help="name recorded on each row")
    parser.add_argument(
        "--blind",
        action="store_true",
        help="hide label, tier, sub-labels and provenance; present rows in a seeded order",
    )
    parser.add_argument(
        "--second",
        action="store_true",
        help="second-annotator pass: write second_label and never touch label (implies --blind)",
    )
    parser.add_argument(
        "--intent-path",
        type=Path,
        default=DEFAULT_INTENT_PATH,
        help="sidecar for the generator's intent on blind synthetic rows",
    )
    subset = parser.add_mutually_exclusive_group()
    subset.add_argument("--ids-file", type=Path, help="label only the ids in this JSON list")
    subset.add_argument(
        "--dev-audit",
        action="store_true",
        help="label only the dev-50 audit sample, drawn from --split-corpus (training plan §4.3)",
    )
    parser.add_argument("--split-corpus", type=Path, default=Path("bench/data/labelled.jsonl"))
    parser.add_argument("--order-seed", type=int, default=DEFAULT_ORDER_SEED)
    args = parser.parse_args(argv)
    # F42: a second annotator who can see the first one's label is not a second annotator.
    args.blind = args.blind or args.second
    return args


def build_session(args: argparse.Namespace) -> LabelSession:
    ids: list[str] | None = None
    if args.ids_file:
        ids = list(json.loads(args.ids_file.read_text(encoding="utf-8")))
    elif args.dev_audit:
        records = [
            CorpusRecord.model_validate_json(line)
            for line in args.split_corpus.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        ids = dev_audit_ids(records)
    return LabelSession(
        args.corpus,
        labeller=args.labeller,
        blind=args.blind,
        second=args.second,
        intent_path=args.intent_path,
        ids=ids,
        order_seed=args.order_seed,
    )


def main(argv: list[str] | None = None) -> int:
    LabelApp(build_session(parse_args(argv))).run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
