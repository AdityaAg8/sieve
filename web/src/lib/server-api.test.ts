import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { server } from "@/mocks/server";

import { loadScanSnapshot } from "./server-api";

/**
 * `serverApiBase()` is `null` under `NEXT_PUBLIC_API_MOCK=1`, which is what the unit environment
 * runs as. Each test says which deployment it is describing rather than inheriting one.
 */
function asDeployedApp(): void {
  vi.stubEnv("NEXT_PUBLIC_API_MOCK", "0");
  vi.stubEnv("NEXT_PUBLIC_API_BASE", "http://localhost:3000");
}

afterEach(() => vi.unstubAllEnvs());

describe("loadScanSnapshot", () => {
  it("does not render on the server when there is no server-side API to read", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
    expect(await loadScanSnapshot("x")).toEqual({ state: "unavailable" });
  });

  it("returns the scan and its findings so the first paint is the real list", async () => {
    asDeployedApp();
    const snapshot = await loadScanSnapshot("x");
    expect(snapshot.state).toBe("ok");
    if (snapshot.state !== "ok") throw new Error("unreachable");
    expect(snapshot.scan.tool_count).toBe(27);
    expect(snapshot.findings).toHaveLength(27);
  });

  it("reports a 404 as missing, which is how an expired scan reads", async () => {
    asDeployedApp();
    server.use(
      http.get("*/api/v1/scans/:scanId", () =>
        HttpResponse.json({ detail: "scan not found" }, { status: 404 }),
      ),
    );
    expect(await loadScanSnapshot("gone")).toEqual({ state: "missing" });
  });

  it("reports an outage as an outage, never as a missing scan", async () => {
    asDeployedApp();
    server.use(
      http.get("*/api/v1/scans/:scanId", () =>
        HttpResponse.json({ detail: "Internal Server Error" }, { status: 500 }),
      ),
    );
    expect(await loadScanSnapshot("x")).toEqual({ state: "error" });
  });

  it("still renders the scan when only the findings page fails", async () => {
    asDeployedApp();
    server.use(
      http.get("*/api/v1/scans/:scanId/findings", () =>
        HttpResponse.json({ detail: "Internal Server Error" }, { status: 500 }),
      ),
    );
    const snapshot = await loadScanSnapshot("x");
    expect(snapshot.state).toBe("ok");
    if (snapshot.state !== "ok") throw new Error("unreachable");
    expect(snapshot.findings).toEqual([]);
  });
});
