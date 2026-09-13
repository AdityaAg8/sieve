"use client";

import { useEffect } from "react";

import type { Verdict } from "./api";

export type FindingKeyHandlers = {
  onMove: (delta: 1 | -1) => void;
  onOpen: () => void;
  onFilter: (verdict: Verdict | null) => void;
  onExport: () => void;
  onHelp: () => void;
  /** False while a dialog owns the keyboard. */
  enabled?: boolean;
};

/** The map the `?` overlay shows, and the single source of truth for the bindings. */
export const KEY_MAP: { keys: string; action: string }[] = [
  { keys: "j", action: "Move down the list" },
  { keys: "k", action: "Move up the list" },
  { keys: "Enter", action: "Open the selected finding" },
  { keys: "1", action: "Show confirmed only" },
  { keys: "2", action: "Show needs review only" },
  { keys: "3", action: "Show dismissed only" },
  { keys: "0", action: "Show every finding" },
  { keys: "e", action: "Export SARIF" },
  { keys: "?", action: "Show this map" },
  { keys: "⌘K / Ctrl K", action: "Open the command palette" },
  { keys: "Esc", action: "Close what is open" },
];

const FILTER_KEYS: Record<string, Verdict | null> = {
  "1": "confirmed",
  "2": "needs_review",
  "3": "dismissed",
  "0": null,
};

/** True when the user is typing, in which case `j` means the letter j. */
function typingInto(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || target.isContentEditable;
}

export function useFindingKeys({
  onMove,
  onOpen,
  onFilter,
  onExport,
  onHelp,
  enabled = true,
}: FindingKeyHandlers): void {
  useEffect(() => {
    if (!enabled) return;

    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (typingInto(event.target)) return;

      if (event.key === "j") {
        event.preventDefault();
        onMove(1);
      } else if (event.key === "k") {
        event.preventDefault();
        onMove(-1);
      } else if (event.key === "Enter") {
        // Enter on a focused control belongs to that control.
        if (event.target instanceof HTMLElement && event.target.closest("button,a")) return;
        event.preventDefault();
        onOpen();
      } else if (event.key in FILTER_KEYS) {
        event.preventDefault();
        onFilter(FILTER_KEYS[event.key]);
      } else if (event.key === "e") {
        event.preventDefault();
        onExport();
      } else if (event.key === "?") {
        event.preventDefault();
        onHelp();
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled, onMove, onOpen, onFilter, onExport, onHelp]);
}
