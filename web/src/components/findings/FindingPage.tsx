"use client";

import Link from "next/link";

import { ErrorNote } from "@/components/shell/ErrorNote";
import { IconArrow } from "@/components/shell/Icons";
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
    <section className="band-hero pattern-gutters">
      <div className="wrap flex flex-col gap-6">
        <Link href={`/scan/${scanId}`} className="link-arrow">
          <IconArrow size={16} className="rotate-180" />
          Back to the scan
        </Link>

        <div>
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
    </section>
  );
}
