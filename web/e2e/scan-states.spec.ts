import { expect, test } from "@playwright/test";

import { EXPECTED, expectRedIsSemantic, mockFail, mockOffline, mockOverride } from "./helpers";

const RESULTS = `/scan/${EXPECTED.scanId}`;

/**
 * The three states of `/scan/[id]` that a real API will not produce on demand, plus the one
 * failure mode that has no status code at all. All of them are mocked deliberately: a scan that
 * expired an hour ago, a worker that gave up, and an API host that is simply not answering are
 * not things a test can ask a live deployment for.
 *
 * The live 404 — the other half of the expiry story — is covered against the real API in
 * `e2e/integration/deep-links.spec.ts`.
 */
test.describe("states the scan page has to tell apart", () => {
  test("names the day a scan expired, when the API still has the row", async ({ page }) => {
    // Expired but not yet purged: the row is there, so the date is real and is printed.
    await mockOverride(page, "scan", { expires_at: "2026-09-12T14:00:00Z" });
    await page.goto(RESULTS);

    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "This scan expired on 12 September 2026.",
    );
    await expect(page.getByRole("link", { name: "Start a new scan" })).toHaveAttribute(
      "href",
      "/scan",
    );
    // An expired scan is Sieve keeping its promise. It is not an error and not a clean scan.
    await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
    await expect(page.getByText(/No findings to triage/)).toHaveCount(0);
  });

  test("a scan that has not expired shows its results, not the expiry state", async ({ page }) => {
    await mockOverride(page, "scan", { expires_at: "2099-01-01T00:00:00Z" });
    await page.goto(RESULTS);

    await expect(page.getByTestId("group-count-confirmed")).toHaveText(String(EXPECTED.confirmed));
    await expect(page.getByText(/^This scan expired/)).toHaveCount(0);
  });

  test("a failed scan states the worker's own reason and offers a retry", async ({ page }) => {
    await mockOverride(page, "scan", {
      status: "failed",
      error: "RemoteFetchError: connection refused",
    });
    await page.goto(RESULTS);

    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "Scan failed: RemoteFetchError: connection refused",
    );
    await expect(page.getByRole("button", { name: "Check this scan again" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Start a new scan" })).toBeVisible();
    // Nothing on a failure page may read as a result.
    await expect(page.getByText(/No findings to triage/)).toHaveCount(0);
    await expectRedIsSemantic(page);
  });

  /**
   * The regression this test exists for: the note used to be gated on an HTTP status, and a
   * refused connection has none. The page showed empty counts and said nothing about why.
   */
  test("a refused connection is explained, not left as blank counts", async ({ page }) => {
    await mockOffline(page, ["scan"]);
    await page.goto(RESULTS);

    await expect(page.getByText("This scan's summary could not be read.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("a 500 on the scan summary is explained the same way", async ({ page }) => {
    await mockFail(page, ["scan"]);
    await page.goto(RESULTS);
    await expect(page.getByText("This scan's summary could not be read.")).toBeVisible();
  });

  test("copying the link reports into the page's one status slot", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(RESULTS);

    await page.getByRole("button", { name: "Copy link" }).click();
    await expect(page.getByText("Link copied.")).toBeVisible();
    // Still exactly one element carrying an explicit `aria-live`, as results.spec.ts requires.
    await expect(page.locator("[aria-live]:not(#__next-route-announcer__)")).toHaveCount(1);
  });
});
