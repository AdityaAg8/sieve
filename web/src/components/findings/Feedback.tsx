"use client";

import { useMutation } from "@tanstack/react-query";

import { sendFeedback } from "@/lib/api";

/**
 * Agree or disagree with a verdict. The `website` field is the honeypot the API expects
 * (PRD §13.1) — it is shipped empty, hidden from everybody, and never focusable, so a human
 * cannot fill it in by accident and a bot that does is discarded server-side.
 */
export function Feedback({ findingId }: { findingId: string }) {
  const mutation = useMutation({
    mutationFn: (agrees: boolean) => sendFeedback(findingId, { agrees, note: null, website: null }),
  });

  return (
    <section aria-label="Feedback" className="flex flex-wrap items-center gap-3">
      <p className="t-ui text-ink-2">Does this verdict look right?</p>

      <div className="flex gap-2">
        <button
          type="button"
          className="btn btn-secondary t-ui"
          disabled={mutation.isPending || mutation.isSuccess}
          onClick={() => mutation.mutate(true)}
        >
          It looks right
        </button>
        <button
          type="button"
          className="btn btn-secondary t-ui"
          disabled={mutation.isPending || mutation.isSuccess}
          onClick={() => mutation.mutate(false)}
        >
          It looks wrong
        </button>
      </div>

      <input type="text" name="website" tabIndex={-1} autoComplete="off" hidden readOnly value="" />

      <p role="status" className="t-ui text-ink-1">
        {mutation.isSuccess ? "Feedback recorded" : null}
        {mutation.isError ? "Feedback was not recorded. Try again in a moment." : null}
      </p>
    </section>
  );
}
