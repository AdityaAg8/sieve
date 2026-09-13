"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { FindingDrawer } from "@/components/findings/FindingDrawer";
import { CommandPalette } from "@/components/palette/CommandPalette";
import { KeyMap } from "@/components/palette/KeyMap";
import { ErrorNote } from "@/components/shell/ErrorNote";
import { VerdictBar } from "@/components/verdict/VerdictBar";
import type { Finding, ScanSummary, Verdict } from "@/lib/api";
import { KEY_MAP, useFindingKeys } from "@/lib/keyboard";
import { useModels } from "@/lib/queries";
import { VERDICT_LABEL, VERDICT_ORDER } from "@/lib/verdict";

import { FindingList } from "./FindingList";
import { useSarifExport } from "./useSarifExport";
import { useScanFeed } from "./useScanFeed";
import { useStreamAnnouncement } from "./useStreamAnnouncement";

/** PRD §13.2 and §14.5: the public demo runs judge-off, so this is the normal state. */
const JUDGE_OFF_NOTICE =
  "The judge is disabled on this deployment; band findings are reported as needs review.";

export function ResultsScreen({
  scanId,
  initialFindingId = null,
  initialScan = null,
  initialFindings = null,
}: {
  scanId: string;
  initialFindingId?: string | null;
  /** What the server renderer already read. See `lib/server-api.ts`. */
  initialScan?: ScanSummary | null;
  initialFindings?: Finding[] | null;
}) {
  const models = useModels();

  const [filter, setFilter] = useState<Verdict | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(initialFindingId);
  const [openId, setOpenId] = useState<string | null>(initialFindingId);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [keyMapOpen, setKeyMapOpen] = useState(false);

  const feed = useScanFeed(scanId, { initialScan, initialFindings, filter });
  const { counts, findings, reserved, retry } = feed;

  const visible = useMemo(
    () => (filter ? findings.filter((f) => f.result.verdict === filter) : findings),
    [findings, filter],
  );

  const ordered = useMemo(
    () =>
      VERDICT_ORDER.flatMap((verdict) => visible.filter((f) => f.result.verdict === verdict)),
    [visible],
  );

  const openFinding = findings.find((f) => f.id === openId) ?? null;
  const { exportSarif, message: exportMessage } = useSarifExport(scanId);

  const move = useCallback(
    (delta: 1 | -1) => {
      if (ordered.length === 0) return;
      const index = ordered.findIndex((f) => f.id === selectedId);
      const next = index === -1 ? 0 : Math.min(ordered.length - 1, Math.max(0, index + delta));
      const target = ordered[next];
      setSelectedId(target.id);
      // Selection moves DOM focus with it, so the row a sighted user sees highlighted is the row
      // a screen reader is on and the row Enter will open.
      const row = document.querySelector<HTMLElement>(
        `[data-finding-id="${CSS.escape(target.id)}"]`,
      );
      row?.scrollIntoView({ block: "nearest" });
      row?.focus({ preventScroll: true });
    },
    [ordered, selectedId],
  );

  const open = useCallback(() => {
    const target = ordered.find((f) => f.id === selectedId) ?? ordered[0];
    if (target) {
      setSelectedId(target.id);
      setOpenId(target.id);
    }
  }, [ordered, selectedId]);

  // The drawer is modal: while it is open the list must not answer j/k/1/2/3/e underneath it.
  const dialogOpen = paletteOpen || keyMapOpen || openId !== null;

  useFindingKeys({
    onMove: move,
    onOpen: open,
    onFilter: setFilter,
    onExport: () => void exportSarif(),
    onHelp: () => setKeyMapOpen(true),
    enabled: !dialogOpen,
  });

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((current) => !current);
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const judgeOff = models.data?.judge_enabled === false;

  const announcement = useStreamAnnouncement(
    feed.status,
    counts.confirmed + counts.needs_review + counts.dismissed,
    feed.expected,
    counts,
    feed.verdictsUnreadable,
  );

  return (
    /*
     * From `lg` up this is a console, not a document: the verdict bar and the filters stay put
     * and the evidence scrolls beside them. That is better for the job — a reviewer keeps the
     * counts in view while reading — and it is also what makes the page's cumulative layout
     * shift structural rather than lucky: the list's box is a fixed size from first paint, so a
     * stream that fills it over two seconds never moves anything outside it.
     */
    <div className="mx-auto max-w-[84rem] px-4 py-10 sm:px-8 lg:flex lg:h-dvh lg:flex-col">
      {/*
        Mounted from first paint and never unmounted, so an assistive technology has something to
        observe the change against. `useStreamAnnouncement` throttles it — 29 verdicts in under a
        second would otherwise be announced 29 times.
      */}
      <p
        data-testid="stream-status"
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
      >
        {announcement}
      </p>

      <header className="flex flex-col gap-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <p className="t-eyebrow">Scan</p>
            <h1 className="t-mono text-ink-1 break-all">{scanId}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className="btn btn-secondary" onClick={() => setPaletteOpen(true)}>
              Search <span className="t-mono text-ink-2">⌘K</span>
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => void exportSarif()}>
              Export SARIF
            </button>
          </div>
        </div>

        <VerdictBar
          scannerFlagged={feed.scan?.scanner_flagged ?? null}
          counts={counts}
          expected={feed.expected}
          streaming={feed.status === "streaming"}
        />

        {/* The slot is reserved whether or not the notice fills it: the judge flag arrives a
            moment after first paint, and a line appearing here would move the whole list. */}
        <div className="min-h-5">
          {judgeOff ? (
            <p className="t-ui text-ink-2 border-line-1 border-l-2 pl-3">{JUDGE_OFF_NOTICE}</p>
          ) : null}
        </div>

        {feed.scanStatus !== null ? (
          <ErrorNote
            title="This scan's summary could not be read."
            detail="The counts above may be incomplete."
            onRetry={retry}
          />
        ) : null}

        {exportMessage ? (
          <p role="status" className="t-ui text-ink-1">
            {exportMessage}
          </p>
        ) : null}
      </header>

      <div className="mt-10 grid gap-8 lg:min-h-0 lg:flex-1 lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-12">
        <aside aria-label="Filters and shortcuts" className="flex flex-col gap-6 lg:self-start">
          <nav aria-label="Filter by verdict">
            <ul className="flex flex-wrap gap-2 lg:flex-col">
              <FilterButton active={filter === null} onClick={() => setFilter(null)}>
                All <span className="t-num text-ink-2">{findings.length}</span>
              </FilterButton>
              {VERDICT_ORDER.map((verdict) => (
                <FilterButton
                  key={verdict}
                  active={filter === verdict}
                  onClick={() => setFilter(verdict)}
                >
                  {VERDICT_LABEL[verdict]} <span className="t-num text-ink-2">{counts[verdict]}</span>
                </FilterButton>
              ))}
            </ul>
          </nav>

          <dl className="hidden flex-col gap-1 lg:flex">
            {KEY_MAP.slice(0, 6).map((entry) => (
              <div key={entry.keys} className="flex items-baseline justify-between gap-3">
                <dt className="t-mono text-ink-2">{entry.keys}</dt>
                <dd className="t-ui text-ink-3 text-right">{entry.action}</dd>
              </div>
            ))}
          </dl>
        </aside>

        <div className="scroll-y-lg lg:min-h-0 lg:pr-3">
          <FindingList
            findings={visible}
            loading={feed.loading}
            toolCount={feed.expected}
            expected={reserved}
            failed={feed.verdictsUnreadable}
            onRetry={retry}
            selectedId={selectedId}
            onOpen={(finding) => {
              setSelectedId(finding.id);
              setOpenId(finding.id);
            }}
          />
        </div>
      </div>

      <FindingDrawer
        finding={openFinding}
        judgeEnabled={models.data?.judge_enabled ?? null}
        onClose={() => setOpenId(null)}
      />

      <CommandPalette
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
        findings={findings}
        onOpenFinding={(finding) => {
          setSelectedId(finding.id);
          setOpenId(finding.id);
        }}
        onFilter={setFilter}
        onExport={() => void exportSarif()}
      />

      <KeyMap open={keyMapOpen} onClose={() => setKeyMapOpen(false)} />
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        aria-pressed={active}
        onClick={onClick}
        className={`btn btn-secondary t-ui u-interactive w-full justify-between gap-3 ${
          active ? "border-ink-0 text-ink-0" : ""
        }`}
      >
        {children}
      </button>
    </li>
  );
}
