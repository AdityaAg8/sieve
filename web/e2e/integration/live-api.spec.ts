import { expect, test } from "@playwright/test";

import { API_BASE_URL, LIVE_BASE_URL, SEEDED_SCAN_ID, models } from "./helpers";

const SEEDED = `/scan/${SEEDED_SCAN_ID}`;
const JUDGE_OFF_NOTICE =
  "The judge is disabled on this deployment; band findings are reported as needs review.";

/**
 * The seeded scan (`python -m sieve.db.seed`, PRD §13.4) is already complete, so these tests are
 * about what the app reads from the real API rather than about what a worker is doing.
 */
test.describe("the app against the live API", () => {
  test("server-renders the scan, so the list is there before JavaScript runs", async ({
    page,
    request,
  }) => {
    const scan = (await (
      await request.get(`${API_BASE_URL}/api/v1/scans/${SEEDED_SCAN_ID}`)
    ).json()) as { tool_count: number; confirmed: number };

    // JavaScript off: whatever is on the page is what the *server* rendered.
    const context = await page.context().browser()?.newContext({ javaScriptEnabled: false });
    if (!context) throw new Error("no browser context");
    const noScript = await context.newPage();
    // A fresh context has no `baseURL`, so the address is spelled out.
    await noScript.goto(`${LIVE_BASE_URL}${SEEDED}`);

    await expect(noScript.locator("[data-finding-id]")).toHaveCount(scan.tool_count);
    await expect(noScript.getByTestId("group-count-confirmed")).toHaveText(String(scan.confirmed));
    await context.close();
  });

  test("the judge notice states the deployment's real judge flag", async ({ page, request }) => {
    const info = await models(request);
    await page.goto(SEEDED);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(SEEDED_SCAN_ID);

    const notice = page.getByText(JUDGE_OFF_NOTICE);
    if (info.judge_enabled) await expect(notice).toBeHidden();
    else await expect(notice).toBeVisible();
  });

  test("the remote tab follows the deployment's own remote flag", async ({ page, request }) => {
    const info = await models(request);
    await page.goto("/scan");
    await expect(page.getByRole("tab", { name: "Paste JSON" })).toBeVisible();

    const remote = page.getByRole("tab", { name: "Remote URL" });
    if (info.remote_enabled) await expect(remote).toBeVisible();
    else await expect(remote).toBeHidden();
  });

  test("Last-Event-ID replays only what a reconnect missed", async ({ request }) => {
    // The contract the hook's reconnect depends on, asserted against the real SSE route rather
    // than against a fake: the browser sends the last `id:` it saw, and the API resumes after it.
    const all = await readStream(request, `${API_BASE_URL}/api/v1/scans/${SEEDED_SCAN_ID}/events`);
    expect(all.ids.length).toBeGreaterThan(1);

    const resumeAfter = all.ids[Math.floor(all.ids.length / 2)];
    const resumed = await readStream(
      request,
      `${API_BASE_URL}/api/v1/scans/${SEEDED_SCAN_ID}/events`,
      { "Last-Event-ID": resumeAfter },
    );

    expect(resumed.ids.length).toBeLessThan(all.ids.length);
    expect(resumed.ids.every((id) => Number(id) > Number(resumeAfter))).toBe(true);
    // The stream still ends where it ended: a resume is not a different stream.
    expect(resumed.ids.at(-1)).toBe(all.ids.at(-1));
  });

  test("a non-integer Last-Event-ID replays from the start rather than failing", async ({
    request,
  }) => {
    const all = await readStream(request, `${API_BASE_URL}/api/v1/scans/${SEEDED_SCAN_ID}/events`);
    const hostile = await readStream(
      request,
      `${API_BASE_URL}/api/v1/scans/${SEEDED_SCAN_ID}/events`,
      { "Last-Event-ID": "'; drop table scans; --" },
    );
    expect(hostile.ids).toEqual(all.ids);
  });

  test("the benchmark page renders whatever /bench/summary really returns", async ({
    page,
    request,
  }) => {
    const summary = (await (
      await request.get(`${API_BASE_URL}/api/v1/bench/summary`)
    ).json()) as { provisional: boolean; headline: Record<string, unknown> };

    await page.goto("/benchmark");
    await expect(page.getByRole("heading", { name: "Headline" })).toBeVisible();

    // Only the half that this deployment's data can falsify: a p-value nobody has measured
    // renders as `—` with "metric to confirm", and a measured one renders as the number. That
    // "No test defined" appears on the rows with no paired comparison is a property of the
    // component and is asserted where it can fail — `HeadlineTable.test.tsx`.
    if (typeof summary.headline.mcnemar_p === "number") {
      await expect(page.getByText(/^McNemar p = /)).toBeVisible();
    } else {
      await expect(page.getByTitle("metric to confirm").first()).toBeVisible();
    }
  });

  test("feedback reaches the real route", async ({ page }) => {
    await page.goto(SEEDED);
    await page.locator("[data-finding-id]").first().click();

    const posted = page.waitForResponse(
      (response) =>
        /\/api\/v1\/findings\/.+\/feedback$/.test(response.url()) &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "It looks right" }).click();
    expect((await posted).status()).toBe(204);
    await expect(page.getByText("Feedback recorded")).toBeVisible();
  });
});

/** Reads a whole SSE response and returns the `id:` values it carried, in order. */
async function readStream(
  request: import("@playwright/test").APIRequestContext,
  url: string,
  headers: Record<string, string> = {},
): Promise<{ ids: string[]; types: string[] }> {
  const response = await request.get(url, { headers: { accept: "text/event-stream", ...headers } });
  expect(response.ok(), `GET ${url}`).toBeTruthy();
  const body = await response.text();
  const line = (prefix: string) =>
    body
      .split(/\r?\n/)
      .filter((l) => l.startsWith(prefix))
      .map((l) => l.slice(prefix.length).trim());
  return { ids: line("id:"), types: line("event:") };
}
