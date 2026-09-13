"use client";

import { useId, useRef, useState } from "react";

import { useModels } from "@/lib/queries";

import { PasteJson } from "./PasteJson";
import { RemoteUrl } from "./RemoteUrl";
import { UploadConfig } from "./UploadConfig";

type TabId = "paste" | "upload" | "remote";

export function ScanTabs() {
  const models = useModels();
  // `remote_enabled` is `SIEVE_ALLOW_REMOTE_MCP` as the deployment reports it (PRD §13.6); the
  // tab stays hidden until `/models` has answered, which is the safe default — the feature is
  // off by default, and a tab that appears and then vanishes is worse than one that is late.
  const showRemote = models.data?.remote_enabled === true;
  const baseId = useId();
  const [active, setActive] = useState<TabId>("paste");
  const tabRefs = useRef(new Map<TabId, HTMLButtonElement>());

  const tabs: { id: TabId; label: string }[] = [
    { id: "paste", label: "Paste JSON" },
    { id: "upload", label: "Upload mcp.json" },
    ...(showRemote ? ([{ id: "remote", label: "Remote URL" }] as const) : []),
  ];

  function onKeyDown(event: React.KeyboardEvent) {
    const order = tabs.map((tab) => tab.id);
    const index = order.indexOf(active);
    let next: TabId | null = null;
    if (event.key === "ArrowRight") next = order[(index + 1) % order.length];
    if (event.key === "ArrowLeft") next = order[(index - 1 + order.length) % order.length];
    if (event.key === "Home") next = order[0];
    if (event.key === "End") next = order[order.length - 1];
    if (!next) return;
    event.preventDefault();
    setActive(next);
    tabRefs.current.get(next)?.focus();
  }

  return (
    <div className="flex flex-col gap-6">
      <div
        role="tablist"
        aria-label="How to give Sieve your tools"
        onKeyDown={onKeyDown}
        className="border-line-1 flex flex-wrap gap-x-1 border-b"
      >
        {tabs.map((tab) => {
          const selected = tab.id === active;
          return (
            <button
              key={tab.id}
              ref={(element) => {
                if (element) tabRefs.current.set(tab.id, element);
                else tabRefs.current.delete(tab.id);
              }}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActive(tab.id)}
              className={`btn btn-quiet u-interactive rounded-none border-b-2 ${
                selected ? "text-ink-0 border-ink-0" : "text-ink-2 hover:text-ink-0 border-transparent"
              }`}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {tabs.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`${baseId}-panel-${tab.id}`}
          aria-labelledby={`${baseId}-tab-${tab.id}`}
          hidden={tab.id !== active}
          tabIndex={0}
        >
          {tab.id === "paste" ? <PasteJson /> : null}
          {tab.id === "upload" ? <UploadConfig /> : null}
          {tab.id === "remote" ? <RemoteUrl /> : null}
        </div>
      ))}
    </div>
  );
}
