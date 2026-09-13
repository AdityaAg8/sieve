"use client";

import type { Finding, Verdict } from "@/lib/api";
import { VERDICT_LABEL, VERDICT_ORDER } from "@/lib/verdict";

import { FindingRow } from "./FindingRow";

type Props = {
  findings: Finding[];
  loading: boolean;
  /** Tools the scan said it would triage; used for the clean-scan sentence. */
  toolCount: number | null;
  selectedId: string | null;
  onOpen: (finding: Finding) => void;
};

/** Confirmed first — the list is ordered by how much of your attention it deserves. */
export function FindingList({ findings, loading, toolCount, selectedId, onOpen }: Props) {
  if (loading && findings.length === 0) return <FindingListSkeleton />;

  if (!loading && findings.length === 0) {
    return (
      <p className="t-body text-ink-1 surface p-6">
        {`No findings to triage. The scanner flagged nothing in these ${toolCount ?? 0} tools.`}
      </p>
    );
  }

  const groups = VERDICT_ORDER.map((verdict) => ({
    verdict,
    items: findings.filter((finding) => finding.result.verdict === verdict),
  })).filter((group) => group.items.length > 0);

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
                onOpen={onOpen}
              />
            ))}
          </ul>
        </section>
      ))}

      {loading ? <FindingListSkeleton rows={2} label="More findings are arriving" /> : null}
    </div>
  );
}

/** Skeletons shaped like the rows they replace — never a spinner (PRD §14.7). */
function FindingListSkeleton({ rows = 5, label = "Reading the scan" }: { rows?: number; label?: string }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="sr-only" aria-live="polite">
        {label}…
      </p>
      {Array.from({ length: rows }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="bg-bg-1 border-line-1 flex flex-col gap-3 rounded-2 border px-4 py-3"
        >
          <div className="flex items-center justify-between gap-4">
            <span className="bg-bg-2 h-4 w-48 rounded-1" />
            <span className="bg-bg-2 h-6 w-28 rounded-1" />
          </div>
          <span className="bg-bg-2 h-px w-full" />
          <span className="bg-bg-2 h-4 w-2/3 rounded-1" />
        </div>
      ))}
    </div>
  );
}

export function verdictCounts(findings: Finding[]): Record<Verdict, number> {
  return {
    confirmed: findings.filter((f) => f.result.verdict === "confirmed").length,
    needs_review: findings.filter((f) => f.result.verdict === "needs_review").length,
    dismissed: findings.filter((f) => f.result.verdict === "dismissed").length,
  };
}
