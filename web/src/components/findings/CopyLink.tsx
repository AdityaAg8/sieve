"use client";

export const COPIED = "Link copied.";
export const COPY_REFUSED = "The link could not be copied. Copy it from the address bar.";

/**
 * Copies this page's own address.
 *
 * A scan's URL is the whole of sharing it: `/scan/{id}` is a deep link, and so is
 * `/scan/{id}/finding/{fid}`. The button copies `location.href` rather than rebuilding an
 * address from props, so whatever the reader is actually looking at is what their colleague
 * opens.
 *
 * The outcome goes to `onResult` instead of into a status element of this component's own:
 * `navigator.clipboard` is permission-gated and can refuse, so the outcome has to be announced —
 * but the results page owns exactly one announced slot, and a second one competing with it is
 * how a screen reader ends up hearing neither. Nothing here animates, so reduced motion has
 * nothing to suppress.
 */
export function CopyLink({ onResult }: { onResult: (message: string) => void }) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      onResult(COPIED);
    } catch {
      onResult(COPY_REFUSED);
    }
  }

  return (
    <button type="button" className="btn btn-secondary btn-sm" onClick={() => void copy()}>
      Copy link
    </button>
  );
}
