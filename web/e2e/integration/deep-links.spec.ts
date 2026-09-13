import { expect, test } from "@playwright/test";

import { API_BASE_URL, SEEDED_SCAN_ID } from "./helpers";

/** A well-formed id the API has never seen. The route is a 404; nothing is mocked to make it one. */
const GONE = "11111111-2222-4333-8444-555555555555";

test.describe("deep links and the states behind them", () => {
  test("a scan the API no longer has reads as expired, not as an error", async ({
    page,
    request,
  }) => {
    const probe = await request.get(`${API_BASE_URL}/api/v1/scans/${GONE}`);
    expect(probe.status(), "the API really does 404 this id").toBe(404);

    await page.goto(`/scan/${GONE}`);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("This scan expired.");
    await expect(page.getByRole("link", { name: "Start a new scan" })).toHaveAttribute(
      "href",
      "/scan",
    );

    // An expired scan is Sieve doing what it promised. Nothing the app renders may read as a
    // fault, and nothing may read as a clean bill of health either. Scoped to `main` because
    // `next dev` parks an empty `role="alert"` of its own beside the dev-tools button.
    await expect(page.locator("main").getByRole("alert")).toHaveCount(0);
    await expect(page.getByText(/No findings to triage/)).toHaveCount(0);
  });

  test("the expired state is server-rendered, so a shared link never flashes a results page", async ({
    page,
  }) => {
    const response = await page.goto(`/scan/${GONE}`);
    expect(response, "a response").toBeTruthy();
    expect(await response!.text()).toContain("This scan expired.");
  });

  test("an address that names nothing answers in the empty-state voice", async ({ page }) => {
    await page.goto("/there-is-no-such-page");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(
      "There is no page at this address.",
    );
    await expect(page.getByRole("link", { name: "Start a new scan" })).toBeVisible();
  });

  test("the results page copies its own address", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.goto(`/scan/${SEEDED_SCAN_ID}`);

    await page.getByRole("button", { name: "Copy link" }).click();
    await expect(page.getByText("Link copied.")).toBeVisible();

    const copied = await page.evaluate(() => navigator.clipboard.readText());
    expect(copied).toBe(page.url());
  });

  test("a deep-linked finding opens on that finding", async ({ page, request }) => {
    const findings = (await (
      await request.get(`${API_BASE_URL}/api/v1/scans/${SEEDED_SCAN_ID}/findings`)
    ).json()) as { items: { id: string }[] };
    const id = findings.items[0].id;

    await page.goto(`/scan/${SEEDED_SCAN_ID}/finding/${encodeURIComponent(id)}`);
    await expect(page.getByRole("heading", { name: id })).toBeVisible();
  });
});
