"use client";

import Link from "next/link";

import { ErrorNote } from "@/components/shell/ErrorNote";
import { useFindings, useModels } from "@/lib/queries";

import { FindingDetail } from "./FindingDrawer";

/**
 * The deep-linkable detail view. Same component as the drawer's body, full width — a link a
 * colleague opens must show exactly what the drawer showed, not a second rendering of it.
 */
export function FindingPage({ scanId, findingId }: { scanId: string; findingId: string }) {
  const findings = useFindings(scanId);
  const models = useModels();
  const finding = findings.data?.items.find((item) => item.id === findingId) ?? null;

  return (
    <div className="mx-auto max-w-[64rem] px-4 py-10 sm:px-8">
      <Link
        href={`/scan/${scanId}`}
        className="t-ui u-interactive text-ink-2 hover:text-ink-0 border-line-2 inline-block border-b pb-0.5"
      >
        Back to the scan
      </Link>

      <div className="mt-6">
        {findings.isError ? (
          <ErrorNote
            title="This finding could not be read."
            detail="The scan's findings did not load. This does not mean the tool is clean."
            onRetry={() => void findings.refetch()}
          />
        ) : findings.isPending ? (
          <p className="surface t-body text-ink-2 p-6" aria-live="polite">
            Reading the scan…
          </p>
        ) : finding ? (
          <div className="surface">
            <FindingDetail finding={finding} judgeEnabled={models.data?.judge_enabled ?? null} />
          </div>
        ) : (
          <p className="surface t-body text-ink-1 p-6">
            This scan has no finding called{" "}
            <span className="t-mono text-ink-0">{findingId}</span>. It may have expired — scans are
            deleted after 7 days.
          </p>
        )}
      </div>
    </div>
  );
}
