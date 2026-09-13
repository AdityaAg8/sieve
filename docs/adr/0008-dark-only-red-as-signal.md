# ADR-008: A dark-only design system where red is signal, not decoration

## Status

Proposed — drafted from PRD §14.1, §14.2 and §14.8 by an agent in Phase 3, against
`web/src/styles/tokens.css` and the checks in `web/scripts/` as built. The human finalises this
ADR's text.

## Context

The front end has one job: make a reviewer believe a dismissal. Most security dashboards fail that
job in the same way, by painting severity in a rainbow and confidence in a gradient until nothing
on the screen means anything in particular. The risk named in PRD §25 is specific: "design drifts
to generic dark-mode-with-accent".

Two supporting constraints: a dual theme doubles every colour decision and every contrast check
for no user who has asked for it (PRD §27), and a colour that is used both for "danger" and for
"brand" cannot be read as either.

## Decision

- **Dark only.** One palette, revisited if a user asks and not before.
- **Every colour and every `font-family` comes from `web/src/styles/tokens.css`.** A hex literal or
  a font stack anywhere else fails `npm run lint:tokens`.
- **Red is semantic and scarce.** `--red-0` for text, borders and marks; `--red-fill` for filled
  chips and the confirmed segment of the verdict bar. It means confirmed, the sieve line, or a
  destructive control, and nothing else.
- **A dismissal is grey and states its class.** Never green: a green tick reads as "this tool is
  safe", and what Sieve actually knows is "the scanner flagged this and it is
  `imperative_benign`". The chip says which class.
- **No invented metrics, no fake browser chrome, no italic headings**, and the empty state for a
  clean scan is the exact sentence in PRD §14.7.
- **The contrast gate is part of the design, not a lint afterthought.** `check-contrast.mjs`
  asserts a minimum ratio for every (foreground, background) pair the UI actually renders, in CI.

## Consequences

- The gate has overruled the PRD's own §14 prose three times, and that is recorded here rather
  than being quietly absorbed:
  1. Phase 0 corrected three token hex values (`ink-3`, `line-2`, `red-fill`) that failed the
     PRD's own WCAG gate as written (commit `d1be436`).
  2. §14.4's dismissed tiles at 60% opacity render `--ink-3` at 2.54:1 on `--bg-0`, below AA, so
     the tiles keep the 0.92 scale and drop the fade (L6 hand-off).
  3. Rows cannot change fill on hover or selection (`--ink-3` on `--bg-2` is 4.20:1), so state
     moves to the border and a 2 px left marker instead.
  The general rule this supports: a design token belongs in a file something can check, not in a
  specification paragraph.
- Scarcity is enforceable. Because red has one meaning, the number of red things on a results
  screen is the number of confirmed findings, which is information rather than styling.
- Dark-only means the dual-theme work is deferred, not free: adding a light theme later means a
  second full pass through the contrast matrix.
- Accessibility is measured (contrast pairs, reduced motion, Lighthouse budgets in
  `web/lighthouserc.json`), and one budget is currently failing on purpose: `/scan/[id]` measures
  CLS 0.09 against a 0.01 budget, which L9's server-rendered first page closes. The budget was
  left asserting the PRD's number rather than relaxed to match what was achieved.
