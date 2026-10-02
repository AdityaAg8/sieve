"use client";

import { useEffect, useRef } from "react";

import { KEY_MAP } from "@/lib/keyboard";

/** The `?` overlay. A native `<dialog>`, so Esc and the focus trap are the browser's job. */
export function KeyMap({ open, onClose }: { open: boolean; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-label="Keyboard map"
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
      className="bg-bg-1 border-line-0 text-ink-1 shadow-[var(--shadow-lift)] m-auto w-[min(28rem,calc(100vw-2rem))] rounded-4 border p-6 backdrop:bg-bg-0/80"
    >
      <h2 className="t-h3 text-ink-0">Keyboard map</h2>
      <dl className="mt-4 flex flex-col gap-2">
        {KEY_MAP.map((entry) => (
          <div key={entry.keys} className="flex items-baseline justify-between gap-6">
            <dt className="t-mono text-ink-1 border-line-0 bg-bg-0 rounded-1 border px-1.5">
              {entry.keys}
            </dt>
            <dd className="t-ui text-ink-2 text-right">{entry.action}</dd>
          </div>
        ))}
      </dl>
      <button type="button" onClick={onClose} className="btn btn-secondary mt-6">
        Close
      </button>
    </dialog>
  );
}
