"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { FindingDrawer } from "@/components/findings/FindingDrawer";
import { CommandPalette } from "@/components/palette/CommandPalette";
import { KeyMap } from "@/components/palette/KeyMap";
import { VerdictBar } from "@/components/verdict/VerdictBar";
import type { Finding, Verdict } from "@/lib/api";
import { KEY_MAP, useFindingKeys } from "@/lib/keyboard";
import { useFindings, useModels, useScan } from "@/lib/queries";
import { useScanEvents } from "@/lib/sse";
import { VERDICT_LABEL, VERDICT_ORDER } from "@/lib/verdict";

import { FindingList, verdictCounts } from "./FindingList";
import { useSarifExport } from "./useSarifExport";

/** PRD §13.2 and §14.5: the public demo runs judge-off, so this is the normal state. */
const JUDGE_OFF_NOTICE =
  "The judge is disabled on this deployment; band findings are reported as needs review.";

export function ResultsScreen({ scanId, initialFindingId = null }: { scanId: string; initialFindingId?: string | null }) {
  const scan = useScan(scanId);
  const models = useModels();
  const live = useScanEvents(scanId);

  // The stream is how this page fills. The paged endpoint is the fallback for a deployment
  // where SSE cannot connect (a proxy that buffers, a browser without EventSource).
  const streamFailed = live.status === "failed";
  const fallback = useFindings(scanId, undefined, { enabled: streamFailed });

  const findings = useMemo<Finding[]>(
    () => (streamFailed ? (fallback.data?.items ?? []) : live.findings),
    [streamFailed, fallback.data, live.findings],
  );

  const [filter, setFilter] = useState<Verdict | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(initialFindingId);
  const [openId, setOpenId] = useState<string | null>(initialFindingId);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [keyMapOpen, setKeyMapOpen] = useState(false);

  const visible = useMemo(
    () => (filter ? findings.filter((f) => f.result.verdict === filter) : findings),
    [findings, filter],
  );

  const ordered = useMemo(
    () =>
      VERDICT_ORDER.flatMap((verdict) => visible.filter((f) => f.result.verdict === verdict)),
    [visible],
  );

  const counts = useMemo(() => verdictCounts(findings), [findings]);
  const openFinding = findings.find((f) => f.id === openId) ?? null;
  const { exportSarif, message: exportMessage } = useSarifExport(scanId);

  const move = useCallback(
    (delta: 1 | -1) => {
      if (ordered.length === 0) return;
      const index = ordered.findIndex((f) => f.id === selectedId);
      const next = index === -1 ? 0 : Math.min(ordered.length - 1, Math.max(0, index + delta));
      const target = ordered[next];
      setSelectedId(target.id);
      document
        .querySelector<HTMLElement>(`[data-finding-id="${CSS.escape(target.id)}"]`)
        ?.scrollIntoView({ block: "nearest" });
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

  const dialogOpen = paletteOpen || keyMapOpen;

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

  const loading = live.status === "connecting" || live.status === "streaming" || fallback.isPending;
  const judgeOff = models.data?.judge_enabled === false;

  return (
    <div className="mx-auto max-w-[84rem] px-4 py-10 sm:px-8">
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
          scannerFlagged={scan.data?.scanner_flagged ?? live.summary?.scanner_flagged ?? null}
          counts={counts}
          expected={live.expected ?? scan.data?.tool_count ?? null}
          streaming={live.status === "streaming"}
        />

        {judgeOff ? (
          <p className="t-ui text-ink-2 border-line-1 border-l-2 pl-3">{JUDGE_OFF_NOTICE}</p>
        ) : null}

        {exportMessage ? (
          <p role="status" className="t-ui text-ink-1">
            {exportMessage}
          </p>
        ) : null}
      </header>

      <div className="mt-10 grid gap-8 lg:grid-cols-[14rem_minmax(0,1fr)] lg:gap-12">
        <aside aria-label="Filters and shortcuts" className="flex flex-col gap-6 lg:sticky lg:top-6 lg:self-start">
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

        <FindingList
          findings={visible}
          loading={loading}
          toolCount={scan.data?.tool_count ?? live.expected ?? null}
          selectedId={selectedId}
          onOpen={(finding) => {
            setSelectedId(finding.id);
            setOpenId(finding.id);
          }}
        />
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
          active ? "border-red-0 text-ink-0" : ""
        }`}
      >
        {children}
      </button>
    </li>
  );
}
