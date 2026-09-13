"use client";

import { useId, useMemo, useState } from "react";

import { validateRemoteUrl } from "@/lib/validate";

import { useCreateScan } from "./useCreateScan";

/**
 * Only rendered when the deployment reports the remote flag. The checks here are a courtesy: the
 * sandboxed worker re-resolves every address and is the actual SSRF boundary (PRD §13.6).
 */
export function RemoteUrl() {
  const fieldId = useId();
  const hintId = `${fieldId}-hint`;
  const [text, setText] = useState("");
  const { start, pending, message } = useCreateScan();

  const result = useMemo(() => validateRemoteUrl(text), [text]);
  const touched = text.trim().length > 0;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (result.ok) start({ source_type: "remote_url", remote_url: result.url });
      }}
    >
      <label htmlFor={fieldId} className="t-ui text-ink-1">
        Remote MCP server URL
      </label>

      <input
        id={fieldId}
        type="url"
        name="remote_url"
        inputMode="url"
        autoComplete="off"
        spellCheck={false}
        value={text}
        onChange={(event) => setText(event.target.value)}
        aria-describedby={hintId}
        aria-invalid={touched && !result.ok}
        placeholder="https://example.com/mcp"
        className="t-mono border-line-2 bg-bg-2 text-ink-1 focus-visible:border-focus min-h-11 w-full rounded-1 border px-3"
      />

      <p
        id={hintId}
        role={touched && !result.ok ? "alert" : undefined}
        className={`t-ui ${touched && !result.ok ? "text-red-0" : "text-ink-2"}`}
      >
        {touched && !result.ok
          ? result.message
          : "Sieve calls tools/list in a sandboxed worker on its own network, follows no redirects, and reads at most 1 MB."}
      </p>

      <div>
        <button type="submit" className="btn btn-primary" disabled={!result.ok || pending}>
          {pending ? "Starting the scan…" : "Triage this server"}
        </button>
      </div>

      {message ? (
        <p role="alert" className="t-ui text-red-0">
          {message}
        </p>
      ) : null}
    </form>
  );
}
