import { describe, expect, it } from "vitest";

import { createScan, getFindings, getModels, getScan, sarifUrl } from "./api";

describe("typed API client against the MSW mocks", () => {
  it("getScan returns the sample scan's real tool count", async () => {
    const scan = await getScan("x");
    expect(scan.tool_count).toBe(27);
  });

  it("getScan reports the scanner's own flag count, which is not the tool count", async () => {
    const scan = await getScan("x");
    expect(scan.scanner_flagged).toBe(4);
    expect(scan.confirmed).toBe(4);
  });

  it("createScan resolves the 202 body with an id", async () => {
    const scan = await createScan({ source_type: "tools_json", tools_json: { tools: [] } });
    expect(scan.id).toEqual(expect.any(String));
    expect(scan.id.length).toBeGreaterThan(0);
  });

  it("getFindings returns a page of every finding, filterable by verdict", async () => {
    const all = await getFindings("x");
    expect(all.total).toBe(27);
    expect(all.items).toHaveLength(27);

    const confirmed = await getFindings("x", { verdict: "confirmed" });
    expect(confirmed.total).toBe(4);
    expect(confirmed.items.every((f) => f.result.verdict === "confirmed")).toBe(true);
  });

  it("getModels reports the deployment's judge flag", async () => {
    const models = await getModels();
    expect(typeof models.judge_enabled).toBe("boolean");
    expect(models.model_version).toEqual(expect.any(String));
  });

  it("sarifUrl points at the scan's SARIF route", () => {
    expect(sarifUrl("abc")).toContain("/api/v1/scans/abc/sarif");
  });
});
