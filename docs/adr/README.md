# Architecture Decision Records

Each record explains one decision a reviewer could challenge, with its context, the decision and
what follows from it. Every record has the same four headings: Status, Context, Decision,
Consequences. New records copy the layout of `docs/adr/0001-three-class-taxonomy.md`.

| ADR | Title | Status |
|---|---|---|
| [ADR-001](0001-three-class-taxonomy.md) | Three-class taxonomy (`benign` / `imperative_benign` / `poisoned`) | Accepted |
| [ADR-002](0002-two-stage-routing.md) | Two-stage routing with an abstain band | Accepted |
| [ADR-003](0003-fpr-at-near-complete-recall.md) | FPR-at-near-complete-recall as the primary metric, with the holdout/CV protocol | Accepted |
| [ADR-004](0004-postgres-queue.md) | Postgres queue instead of Redis | Accepted |
| [ADR-005](0005-onnx-int8-serving.md) | ONNX fp32 serving (int8 rejected) | Accepted |
| [ADR-006](0006-scanner-agnostic-model.md) | Scanner-agnostic model by default | Accepted |
| [ADR-007](0007-no-glama-data-licensing-split.md) | No Glama data, and the licensing split | Accepted |
| [ADR-008](0008-dark-only-red-as-signal.md) | Dark-only, red-as-signal design system | Accepted |
| [ADR-009](0009-training-v1-deviations.md) | Training v1 deviations from the original design (ensemble labels, deferred judge, gates) | Accepted |

A decision that is revisited gets a new record that supersedes the old one; accepted records are
not edited in place. All nine records were reworded when they were accepted on 2026-10-14.
