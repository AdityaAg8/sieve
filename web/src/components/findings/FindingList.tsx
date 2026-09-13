"use client";

import { ErrorNote } from "@/components/shell/ErrorNote";
import type { Finding, Verdict } from "@/lib/api";
import { VERDICT_LABEL, VERDICT_ORDER } from "@/lib/verdict";

import { FindingRow } from "./FindingRow";

export type VerdictCounts = Record<Verdict, number>;

type Props = {
  findings: Finding[];
  loading: boolean;
  /** Tools the scan said it would triage; used for the clean-scan sentence. */
  toolCount: number | null;
  /**
   * The scan's own final counts, when known. Each group reserves that many rows from first
   * paint, so a `confirmed` verdict arriving after twenty `dismissed` ones drops into a slot
   * that is already there instead of pushing the rest of the list down — which is the whole of
   * this page's cumulative layout shift.
   */
  expected: VerdictCounts | null;
  /** The scan could not be read. Never the same branch as "the scan found nothing". */
  failed?: boolean;
  onRetry?: () => void;
  selectedId: string | null;
  onOpen: (finding: Finding) => void;
};

/** Confirmed first — the list is ordered by how much of your attention it deserves. */
export function FindingList({
  findings,
  loading,
  toolCount,
  expected,
  failed = false,
  onRetry,
  selectedId,
  onOpen,
}: Props) {
  const arrived = verdictCounts(findings);

  // An outage is not a clean scan. This branch comes first so no failure path can fall through
  // to the empty state's "the scanner flagged nothing" sentence.
  if (failed && findings.length === 0) {
    return (
      <ErrorNote
        title="This scan could not be read."
        detail="The verdicts did not arrive. Nothing here says the tools are clean — it says Sieve could not tell you."
        onRetry={onRetry}
      />
    );
  }

  // Until the scan says how many rows each group will hold there is nothing to reserve them
  // with, so the list waits rather than rendering groups it would have to re-order — and move
  // every row already on screen — a moment later.
  if (expected === null) {
    return (
      <p className="sr-only" aria-live="polite">
        Reading the scan…
      </p>
    );
  }

  // The clean-scan sentence names a real count or it is not shown: a `0` here would be an
  // invented number dressed as reassurance.
  if (!loading && findings.length === 0) {
    return typeof toolCount === "number" ? (
      <p className="t-body text-ink-1 surface p-6">
        {`No findings to triage. The scanner flagged nothing in these ${toolCount} tools.`}
      </p>
    ) : (
      <ErrorNote
        title="This scan could not be read."
        detail="Sieve did not receive the scan's tool count, so it cannot say what was triaged."
        onRetry={onRetry}
      />
    );
  }

  // One tab stop for the whole list: the selected row, or the first one when nothing is.
  const ordered = VERDICT_ORDER.flatMap((verdict) =>
    findings.filter((finding) => finding.result.verdict === verdict),
  );
  const tabStopId = ordered.some((finding) => finding.id === selectedId)
    ? selectedId
    : (ordered[0]?.id ?? null);

  const groups = VERDICT_ORDER.map((verdict) => ({
    verdict,
    items: findings.filter((finding) => finding.result.verdict === verdict),
    reserved: Math.max(0, (expected?.[verdict] ?? arrived[verdict]) - arrived[verdict]),
  })).filter((group) => group.items.length > 0 || group.reserved > 0);

  return (
    <div className="flex flex-col gap-8">
      {groups.map((group) => (
        <section key={group.verdict} aria-labelledby={`group-${group.verdict}`}>
          <h2
            id={`group-${group.verdict}`}
            className="t-eyebrow border-line-1 flex items-baseline justify-between gap-3 border-b pb-2"
          >
            <span>{VERDICT_LABEL[group.verdict]}</span>
            <span className="t-num" data-testid={`group-count-${group.verdict}`}>
              {group.items.length}
            </span>
          </h2>

          <ul className="mt-3 flex flex-col gap-2">
            {group.items.map((finding) => (
              <FindingRow
                key={finding.id}
                finding={finding}
                selected={finding.id === selectedId}
                tabStop={finding.id === tabStopId}
                onOpen={onOpen}
              />
            ))}
            {Array.from({ length: group.reserved }, (_, index) => (
              <PendingRow key={`pending-${index}`} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** A reserved slot, shaped like the row that will replace it. */
function PendingRow() {
  return (
    <li aria-hidden="true">
      <div className="bg-bg-1 border-line-1 flex flex-col gap-3 rounded-2 border px-4 py-3 sm:h-28">
        <div className="flex items-center justify-between gap-4">
          <span className="bg-bg-2 h-4 w-48 rounded-1" />
          <span className="bg-bg-2 h-6 w-28 rounded-1" />
        </div>
        <span className="bg-line-1 h-px w-full" />
        <span className="bg-bg-2 h-4 w-2/3 rounded-1" />
      </div>
    </li>
  );
}

export function verdictCounts(findings: Finding[]): VerdictCounts {
  return {
    confirmed: findings.filter((f) => f.result.verdict === "confirmed").length,
    needs_review: findings.filter((f) => f.result.verdict === "needs_review").length,
    dismissed: findings.filter((f) => f.result.verdict === "dismissed").length,
  };
}
