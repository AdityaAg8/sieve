"use client";

import { useCallback, useSyncExternalStore } from "react";

/** Past this, `setTimeout` fires immediately instead of late, which is the opposite of the point. */
const MAX_DELAY_MS = 2 ** 31 - 1;

/**
 * Has this moment passed?
 *
 * The awkward part is that the answer changes with no input changing, which is precisely what a
 * render is not allowed to notice (`react-hooks/purity`), and setting state from an effect to
 * work around it is its own rule (`react-hooks/set-state-in-effect`). `useSyncExternalStore` is
 * the shape React provides for exactly this: the clock is the external store, `getSnapshot`
 * reads it, and the subscription is a single timer that fires when the deadline arrives — so a
 * scan that expires while its page is open says so, without polling and without a reload.
 *
 * The server snapshot is always `false`. The server's clock is not the reader's, and a page that
 * server-renders "expired" and then hydrates to "not expired" is a hydration mismatch dressed up
 * as a feature; the API's own 404 is the authority on a scan that is really gone.
 */
export function useHasPassed(iso: string | null | undefined): boolean {
  const at = iso ? Date.parse(iso) : Number.NaN;
  const deadline = Number.isNaN(at) ? null : at;

  const subscribe = useCallback(
    (onChange: () => void) => {
      if (deadline === null) return () => {};
      const due = deadline - Date.now();
      if (due <= 0 || due > MAX_DELAY_MS) return () => {};
      const timer = window.setTimeout(onChange, due);
      return () => window.clearTimeout(timer);
    },
    [deadline],
  );

  const snapshot = useCallback(
    () => deadline !== null && deadline <= Date.now(),
    [deadline],
  );

  return useSyncExternalStore(subscribe, snapshot, () => false);
}
