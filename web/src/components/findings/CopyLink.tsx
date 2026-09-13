"use client";

import { useState } from "react";

const COPIED = "Link copied.";
const REFUSED = "The link could not be copied. Copy it from the address bar.";

/**
 * Copies this page's own address.
 *
 * A scan's URL is the whole of sharing it: `/scan/{id}` is a deep link, and so is
 * `/scan/{id}/finding/{fid}`. The button copies `location.href` rather than rebuilding an
 * address from props, so whatever the reader is actually looking at is what their colleague
 * opens.
 *
 * The outcome is announced rather than merely shown: `navigator.clipboard` is permission-gated
 * and can refuse, and a button that silently does nothing is worse than one that says it could
 * not. Nothing here animates, so reduced motion has nothing to suppress.
 */
export function CopyLink() {
  const [message, setMessage] = useState<string | null>(null);

  async function copy() {
    setMessage(null);
    try {
      await navigator.clipboard.writeText(window.location.href);
      setMessage(COPIED);
    } catch {
      setMessage(REFUSED);
    }
  }

  return (
    <>
      <button type="button" className="btn btn-secondary" onClick={() => void copy()}>
        Copy link
      </button>
      {/* `role="status"` and nothing more: the results page owns exactly one element carrying an
          explicit `aria-live` (its stream status), and `e2e/results.spec.ts` enforces that. */}
      <span role="status" className="t-ui text-ink-2">
        {message}
      </span>
    </>
  );
}
