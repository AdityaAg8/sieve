"use client";

import type { Finding } from "@/lib/api";
import { subLabelTitle } from "@/lib/owasp";
import { CLASS_LABEL, stageNote } from "@/lib/verdict";

import { ConfidenceBar } from "./ConfidenceBar";
import { DismissalReason } from "./DismissalReason";
import { VerdictChip } from "./VerdictChip";

type Props = {
  finding: Finding;
  selected: boolean;
  /** The list's single tab stop. Roving tabindex: one Tab enters the list, j/k move inside it. */
  tabStop: boolean;
  onOpen: (finding: Finding) => void;
};

/**
 * One row of the triage list. Hover and selection move the row's border and its left marker —
 * never its fill — because `--ink-3` (dismissed) is only contrast-safe on `--bg-0` and
 * `--bg-1`. See web/DESIGN.md §3.
 */
export function FindingRow({ finding, selected, tabStop, onOpen }: Props) {
  const { result } = finding;
  const scanner = result.scanner_findings[0];
  const note = stageNote(result);

  const marker = {
    confirmed: "before:bg-red-fill",
    needs_review: "before:bg-ink-0",
    dismissed: "before:bg-ink-3",
  }[result.verdict];

  return (
    <li>
      <button
        type="button"
        data-finding-id={finding.id}
        data-verdict={result.verdict}
        data-red={result.verdict === "confirmed" ? "confirmed" : undefined}
        aria-current={selected ? "true" : undefined}
        tabIndex={tabStop ? 0 : -1}
        onClick={() => onOpen(finding)}
        className={`u-interactive bg-bg-1 relative w-full rounded-2 border px-4 py-3 text-left before:absolute before:inset-y-2 before:left-0 before:w-0.5 before:content-[''] sm:h-28 ${marker} ${
          selected ? "border-line-2" : "border-line-1 hover:border-line-2"
        }`}
      >
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <span className="t-mono text-ink-0 break-all">{finding.id}</span>
          <div className="flex items-center gap-3">
            <ConfidenceBar
              value={result.confidence}
              tone={result.verdict === "confirmed" ? "red" : "ink"}
            />
            <VerdictChip verdict={result.verdict} />
          </div>
        </div>

        {result.sub_labels.length > 0 ? (
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {result.sub_labels.map((label) => (
              <li
                key={label}
                className="t-ui text-ink-2 border-line-1 rounded-1 border px-1.5 py-0.5"
              >
                {subLabelTitle(label)}
              </li>
            ))}
          </ul>
        ) : null}

        <dl className="border-line-1 mt-3 grid gap-x-6 gap-y-2 border-t pt-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1">
            <dt className="t-eyebrow">Scanner said</dt>
            <dd className="t-ui text-ink-1">
              {scanner ? (
                <>
                  <span className="t-mono">{scanner.scanner}</span>
                  {scanner.rule_id ? (
                    <>
                      {" · "}
                      <span className="t-mono">{scanner.rule_id}</span>
                    </>
                  ) : null}
                  {scanner.severity ? ` · ${scanner.severity}` : null}
                </>
              ) : (
                <span className="text-ink-2">No finding</span>
              )}
            </dd>
          </div>

          <div className="flex flex-col gap-1">
            <dt className="t-eyebrow">Sieve said</dt>
            <dd className="t-ui text-ink-1">
              {result.verdict === "dismissed" ? (
                <DismissalReason result={result} />
              ) : (
                <span className="t-num">
                  {CLASS_LABEL[result.predicted_class]}
                  {note ? ` · ${note}` : ""}
                </span>
              )}
            </dd>
          </div>
        </dl>
      </button>
    </li>
  );
}
