"use client";

import { useCallback, useState } from "react";

import { sarifUrl } from "@/lib/api";

/**
 * Downloads the scan's SARIF 2.1.0 document. Fetched rather than linked so a failure can be
 * explained on the page instead of replacing it with a browser error, and so the mocked route
 * is exercised the same way the real one will be.
 */
export function useSarifExport(scanId: string) {
  const [message, setMessage] = useState<string | null>(null);

  const exportSarif = useCallback(async () => {
    setMessage(null);
    try {
      const response = await fetch(sarifUrl(scanId), { headers: { accept: "application/json" } });
      if (!response.ok) throw new Error(String(response.status));
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = href;
      anchor.download = `${scanId}.sarif`;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(href);
      setMessage("SARIF exported.");
    } catch {
      setMessage("The SARIF export failed. Try again, or use the CLI.");
    }
  }, [scanId]);

  return { exportSarif, message };
}
