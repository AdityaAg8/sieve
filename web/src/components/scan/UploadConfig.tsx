"use client";

import { useId, useState } from "react";

import { validateMcpConfig, type ConfigResult } from "@/lib/validate";

import { useCreateScan } from "./useCreateScan";

/**
 * The file is read in the browser with `File.text()` and parsed here. It is never uploaded to a
 * third party, never posted to an analytics endpoint, and only its parsed contents travel — to
 * the scan endpoint the user asked for, and nowhere else.
 */
export function UploadConfig() {
  const fieldId = useId();
  const hintId = `${fieldId}-hint`;
  const [fileName, setFileName] = useState<string | null>(null);
  const [result, setResult] = useState<ConfigResult | null>(null);
  const { start, pending, message } = useCreateScan();

  async function onFile(file: File | undefined) {
    if (!file) {
      setFileName(null);
      setResult(null);
      return;
    }
    setFileName(file.name);
    if (!file.name.toLowerCase().endsWith(".json")) {
      setResult({ ok: false, message: "Choose a .json file. Sieve reads mcp.json only." });
      return;
    }
    setResult(validateMcpConfig(await file.text()));
  }

  const ready = result?.ok === true;

  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (result?.ok) start({ source_type: "mcp_config", mcp_config: result.payload });
      }}
    >
      <label htmlFor={fieldId} className="t-ui text-ink-1">
        Your mcp.json
      </label>

      <input
        id={fieldId}
        type="file"
        name="mcp_config"
        accept=".json,application/json"
        aria-describedby={hintId}
        onChange={(event) => void onFile(event.target.files?.[0])}
        className="t-ui text-ink-1 border-line-2 file:btn file:btn-secondary file:mr-3 w-full rounded-1 border border-dashed p-3"
      />

      <p
        id={hintId}
        role={result && !result.ok ? "alert" : undefined}
        className={`t-ui ${result && !result.ok ? "text-red-0" : "text-ink-2"}`}
      >
        {result?.ok
          ? `${fileName} lists ${result.serverCount} ${result.serverCount === 1 ? "server" : "servers"}: ${result.serverNames.join(", ")}.`
          : result
            ? result.message
            : "Choose the mcp.json your client reads. It is parsed in your browser and never uploaded anywhere else."}
      </p>

      <div>
        <button type="submit" className="btn btn-primary" disabled={!ready || pending}>
          {pending
            ? "Starting the scan…"
            : result?.ok
              ? `Triage ${result.serverCount} ${result.serverCount === 1 ? "server" : "servers"}`
              : "Triage servers"}
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
