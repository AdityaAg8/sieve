# Architecture Decision Records

Every row in PRD §7.2 that a reviewer could challenge gets an ADR here. Phase 0 drafts each
record's title and status; the human writes the final decision text (PRD §7.3).

| ADR | Title | Status |
|---|---|---|
| [ADR-001](0001-three-class-taxonomy.md) | Three-class taxonomy (`benign` / `imperative_benign` / `poisoned`) | proposed |
| ADR-002 | Two-stage routing with an abstain band | proposed |
| ADR-003 | FPR-at-near-complete-recall as the primary metric, with the holdout/CV protocol | proposed |
| ADR-004 | Postgres queue instead of Redis | proposed |
| ADR-005 | ONNX int8 serving | proposed |
| ADR-006 | Scanner-agnostic model by default | proposed |
| ADR-007 | No Glama data, and the licensing split | proposed |
| ADR-008 | Dark-only, red-as-signal design system | proposed |

`bench/versions.lock` (created in Task 0.1, filled with `cisco_yara=<version>` in Task 0.5) is
tracked separately from this index; there is nothing for it to record here.
