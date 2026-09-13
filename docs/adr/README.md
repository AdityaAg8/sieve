# Architecture Decision Records

Every row in PRD §7.2 that a reviewer could challenge gets an ADR here. Phase 0 drafted ADR-001;
Phase 3 (lane L10) drafted ADR-002 to ADR-008 from PRD §7.3 and §27 against the code as built.
**The human writes the final text of each record** (PRD §7.3); every file below still says
`Proposed` and names the sections its author has to own.

Each record keeps the same four headings: Status, Context, Decision, Consequences.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](0001-three-class-taxonomy.md) | Three-class taxonomy (`benign` / `imperative_benign` / `poisoned`) | proposed |
| [ADR-002](0002-two-stage-routing.md) | Two-stage routing with an abstain band | proposed |
| [ADR-003](0003-fpr-at-near-complete-recall.md) | FPR-at-near-complete-recall as the primary metric, with the holdout/CV protocol | proposed |
| [ADR-004](0004-postgres-queue.md) | Postgres queue instead of Redis | proposed |
| [ADR-005](0005-onnx-int8-serving.md) | ONNX int8 serving | proposed |
| [ADR-006](0006-scanner-agnostic-model.md) | Scanner-agnostic model by default | proposed |
| [ADR-007](0007-no-glama-data-licensing-split.md) | No Glama data, and the licensing split | proposed |
| [ADR-008](0008-dark-only-red-as-signal.md) | Dark-only, red-as-signal design system | proposed |

PRD §27 records the revisit trigger for several of these decisions (queue throughput, ONNX parity,
judge model, theme, remote scanning). A decision that is revisited gets a new record that
supersedes the old one; accepted records are not edited in place.

`bench/versions.lock` (created in Task 0.1, filled with `cisco_yara=<version>` in Task 0.5) is
tracked separately from this index; there is nothing for it to record here.
