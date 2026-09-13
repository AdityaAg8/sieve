"use client";

import { Command } from "cmdk";
import { useRouter } from "next/navigation";

import type { Finding, Verdict } from "@/lib/api";
import { VERDICT_LABEL } from "@/lib/verdict";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  findings: Finding[];
  onOpenFinding: (finding: Finding) => void;
  onFilter: (verdict: Verdict | null) => void;
  onExport: () => void;
};

/**
 * ⌘K. Everything the keyboard map can do, plus jumping straight to a tool by name — which is
 * the thing a reviewer actually wants when a colleague says "look at export_csv".
 */
export function CommandPalette({
  open,
  onOpenChange,
  findings,
  onOpenFinding,
  onFilter,
  onExport,
}: Props) {
  const router = useRouter();

  const run = (action: () => void) => {
    onOpenChange(false);
    action();
  };

  return (
    <Command.Dialog
      open={open}
      onOpenChange={onOpenChange}
      label="Command palette"
      className="bg-bg-1 border-line-2 fixed top-[12vh] left-1/2 z-50 w-[min(38rem,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-3 border"
      overlayClassName="bg-bg-0/80 fixed inset-0 z-40"
    >
      <Command.Input
        placeholder="Search tools, or type a command…"
        className="t-body text-ink-0 border-line-1 placeholder:text-ink-2 w-full border-b bg-transparent px-4 py-3 outline-none"
      />

      <Command.List className="max-h-[50vh] overflow-y-auto p-2">
        <Command.Empty className="t-ui text-ink-2 px-3 py-4">
          Nothing matches that. Try a tool name, or a verdict.
        </Command.Empty>

        <Command.Group heading="Filter" className="t-eyebrow px-2 pt-2 pb-1">
          {(["confirmed", "needs_review", "dismissed"] as Verdict[]).map((verdict) => (
            <Item key={verdict} onSelect={() => run(() => onFilter(verdict))}>
              Show {VERDICT_LABEL[verdict].toLowerCase()} only
            </Item>
          ))}
          <Item onSelect={() => run(() => onFilter(null))}>Show every finding</Item>
        </Command.Group>

        <Command.Group heading="Do" className="t-eyebrow px-2 pt-3 pb-1">
          <Item onSelect={() => run(onExport)}>Export SARIF</Item>
          <Item onSelect={() => run(() => router.push("/benchmark"))}>Open the benchmark</Item>
          <Item onSelect={() => run(() => router.push("/docs"))}>Open the docs</Item>
          <Item onSelect={() => run(() => router.push("/scan"))}>Start a new scan</Item>
        </Command.Group>

        {findings.length > 0 ? (
          <Command.Group heading="Tools" className="t-eyebrow px-2 pt-3 pb-1">
            {findings.map((finding) => (
              <Item key={finding.id} onSelect={() => run(() => onOpenFinding(finding))}>
                <span className="t-mono">{finding.id}</span>
                <span className="t-ui text-ink-2 ml-auto">
                  {VERDICT_LABEL[finding.result.verdict]}
                </span>
              </Item>
            ))}
          </Command.Group>
        ) : null}
      </Command.List>
    </Command.Dialog>
  );
}

function Item({ children, onSelect }: { children: React.ReactNode; onSelect: () => void }) {
  return (
    <Command.Item
      onSelect={onSelect}
      className="t-body text-ink-1 data-[selected=true]:bg-bg-3 data-[selected=true]:text-ink-0 flex cursor-pointer items-center gap-2 rounded-1 px-3 py-2"
    >
      {children}
    </Command.Item>
  );
}
