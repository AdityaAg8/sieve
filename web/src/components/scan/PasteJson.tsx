"use client";

import { useId, useMemo, useState } from "react";

import { validateToolsJson } from "@/lib/validate";

import { useCreateScan } from "./useCreateScan";

const HINT = "Paste a tools/list JSON to begin.";

export function PasteJson({ initialText = "" }: { initialText?: string }) {
  const [text, setText] = useState(initialText);
  const fieldId = useId();
  const hintId = `${fieldId}-hint`;
  const { start, pending, message } = useCreateScan();

  const result = useMemo(() => validateToolsJson(text), [text]);
  const touched = text.trim().length > 0;
  const ready = result.ok;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (result.ok) start({ source_type: "tools_json", tools_json: result.payload });
      }}
    >
      <label htmlFor={fieldId} className="t-ui text-ink-1">
        Tools JSON
      </label>

      <textarea
        id={fieldId}
        name="tools"
        value={text}
        onChange={(event) => setText(event.target.value)}
        spellCheck={false}
        autoComplete="off"
        rows={14}
        aria-describedby={hintId}
        aria-invalid={touched && !ready}
        placeholder='{ "tools": [ … ] }'
        className="quote border-line-2 focus-visible:border-focus w-full resize-y p-3"
      />

      <p
        id={hintId}
        role={touched && !ready ? "alert" : undefined}
        className={`t-ui ${touched && !ready ? "text-red-0" : "text-ink-2"}`}
      >
        {ready
          ? `Ready to triage ${result.toolCount} ${result.toolCount === 1 ? "tool" : "tools"}.`
          : touched
            ? result.message
            : HINT}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <button type="submit" className="btn btn-primary" disabled={!ready || pending}>
          {pending
            ? "Starting the scan…"
            : ready
              ? `Triage ${result.toolCount} ${result.toolCount === 1 ? "tool" : "tools"}`
              : "Triage tools"}
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
