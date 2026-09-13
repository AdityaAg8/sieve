import { expect, test } from "@playwright/test";

import { API_BASE_URL, SAMPLE_TOOL_COUNT, allFindings, startSampleScan } from "./helpers";

/**
 * PRD §22's path, end to end, against the real API: paste the sample, watch the results fill from
 * the live event stream, open a finding, export SARIF.
 *
 * Nothing in this file is mocked. The scan is created by `POST /api/v1/scans`, triaged by the
 * worker draining the Postgres queue, streamed by `GET /api/v1/scans/{id}/events` over the
 * browser's own `EventSource`, and exported by `GET /api/v1/scans/{id}/sarif`. A results page
 * that cannot produce a valid SARIF file is not integrated, and this is the only test that
 * checks the real export end to end.
 */
test.describe("the §22 path against the real API", () => {
  test("paste sample → live results → open a finding → export SARIF", async ({ page, request }) => {
    const scanId = await startSampleScan(page);

    // --- live results -------------------------------------------------------------------
    // The counts start empty and fill from the stream. Waiting on the total rather than on any
    // one verdict keeps the assertion true whatever the model decides about these tools.
    const bar = page.getByTestId("verdict-bar-count-confirmed");
    await expect(bar).toBeVisible();

    await expect
      .poll(
        async () => {
          const response = await request.get(`${API_BASE_URL}/api/v1/scans/${scanId}`);
          return response.ok() ? ((await response.json()) as { status: string }).status : "unread";
        },
        { timeout: 60_000, message: "the worker finishes the scan" },
      )
      .toBe("complete");

    const rows = page.locator("[data-finding-id]");
    await expect.poll(async () => rows.count(), { timeout: 30_000 }).toBe(SAMPLE_TOOL_COUNT);

    const scan = (await (await request.get(`${API_BASE_URL}/api/v1/scans/${scanId}`)).json()) as {
      tool_count: number;
      scanner_flagged: number;
      confirmed: number;
      needs_review: number;
      dismissed: number;
    };
    expect(scan.tool_count).toBe(SAMPLE_TOOL_COUNT);

    // The page's own counts are the API's counts. Read from the API, never hard-coded: the
    // fixture's verdicts are the model's to decide.
    await expect(page.getByTestId("verdict-bar-count-confirmed")).toHaveText(
      String(scan.confirmed),
    );
    await expect(page.getByTestId("verdict-bar-count-needs_review")).toHaveText(
      String(scan.needs_review),
    );
    await expect(page.getByTestId("verdict-bar-count-dismissed")).toHaveText(
      String(scan.dismissed),
    );
    await expect(
      page.getByText(`The scanner flagged ${scan.scanner_flagged}. Sieve confirmed ${scan.confirmed}.`),
    ).toBeVisible();

    // --- open a finding -----------------------------------------------------------------
    const first = rows.first();
    const findingId = await first.getAttribute("data-finding-id");
    expect(findingId).toBeTruthy();
    await first.click();

    const drawer = page.getByRole("dialog", { name: `Finding ${findingId}` });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByRole("heading", { name: findingId as string })).toBeVisible();
    await expect(drawer.getByRole("heading", { name: "Definition" })).toBeVisible();
    await drawer.getByRole("button", { name: "Close" }).click();
    await expect(drawer).toBeHidden();

    // --- export SARIF -------------------------------------------------------------------
    // The control itself: the page fetches the document and says so. A download the browser
    // never received would leave this sentence unsaid.
    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export SARIF" }).click();
    expect((await download).suggestedFilename()).toBe(`${scanId}.sarif`);
    await expect(page.getByText("SARIF exported.")).toBeVisible();

    // The document itself, fetched from the real route the button fetched.
    const sarifResponse = await request.get(`${API_BASE_URL}/api/v1/scans/${scanId}/sarif`);
    expect(sarifResponse.ok(), "GET /scans/{id}/sarif").toBeTruthy();
    const sarif = (await sarifResponse.json()) as {
      version: string;
      runs: {
        results: {
          level: string;
          properties: { predicted_class?: string; finding_id?: string };
        }[];
      }[];
    };

    expect(sarif.version).toBe("2.1.0");
    expect(sarif.runs).toHaveLength(1);

    // Every page of them, not the first: the SARIF document has one result per finding, so the
    // comparison is only as good as this list.
    const findings = await allFindings(request, scanId);

    // One result per finding — not per confirmed finding, and not per flagged tool.
    expect(sarif.runs[0].results).toHaveLength(findings.length);
    expect(findings).toHaveLength(SAMPLE_TOOL_COUNT);

    const byId = new Map(findings.map((f) => [f.id, f.result.verdict]));
    for (const result of sarif.runs[0].results) {
      // `properties.predicted_class` is present on every result, whatever the verdict was.
      expect(result.properties.predicted_class, JSON.stringify(result.properties)).toBeTruthy();
      const verdict = byId.get(String(result.properties.finding_id));
      expect(verdict, `finding ${result.properties.finding_id} is in the scan`).toBeTruthy();
      if (verdict === "confirmed") expect(result.level).toBe("error");
    }

    // And the claim holds in the other direction: every confirmed finding is an `error`.
    const confirmedIds = findings
      .filter((f) => f.result.verdict === "confirmed")
      .map((f) => f.id);
    const errorIds = sarif.runs[0].results
      .filter((r) => r.level === "error")
      .map((r) => String(r.properties.finding_id));
    expect([...errorIds].sort()).toEqual([...confirmedIds].sort());
  });
});
