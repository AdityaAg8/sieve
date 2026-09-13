import { expect, test } from "@playwright/test";

import {
  EXPECTED,
  expectRedIsSemantic,
  mockCleanScan,
  mockFail,
  mockOverride,
} from "./helpers";

const RESULTS = `/scan/${EXPECTED.scanId}`;

test.describe("red is semantic, everywhere", () => {
  test("on the landing page", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("figure", { name: "The sieve" })).toBeVisible();
    await expectRedIsSemantic(page);
  });

  test("on the scan input", async ({ page }) => {
    await page.goto("/scan");
    await expect(page.getByRole("tab", { name: "Paste JSON" })).toBeVisible();
    await expectRedIsSemantic(page);

    // A validation error is the destructive role, so it may be red — and the tab underline
    // beside it must not be, or the two would mean the same thing.
    await page.getByLabel("Tools JSON").fill("{ broken");
    await expect(page.locator("p[role=alert]")).toBeVisible();
    await expectRedIsSemantic(page);
  });

  test("on the results screen, with a finding open", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-dismissed")).toHaveText(String(EXPECTED.dismissed));
    await expectRedIsSemantic(page);

    await page.getByRole("button", { name: /weather\/set_location/ }).click();
    await expect(page.getByRole("dialog", { name: /^Finding / })).toBeVisible();
    await expectRedIsSemantic(page);
  });

  test("on the benchmark, measured", async ({ page }) => {
    await mockOverride(page, "bench", {
      provisional: false,
      dataset_version: "d",
      model_version: "m",
      generated_at: "2026-09-13T00:00:00Z",
      headline: {},
      tables: {
        per_tier: [{ bucket: "easy", recall: 0.9, fpr: 0.05, n: 10 }],
        confusion: {
          labels: ["benign", "imperative_benign", "poisoned"],
          counts: [
            [1, 0, 0],
            [0, 1, 2],
            [0, 0, 1],
          ],
          called_out: [1, 2],
        },
      },
    });
    await page.goto("/benchmark");
    await expect(page.getByRole("region", { name: "Headline" })).toBeVisible();
    await expectRedIsSemantic(page);
  });

  test("on the docs", async ({ page }) => {
    await page.goto("/docs/quickstart");
    await expect(page.getByRole("heading", { name: "Quickstart", level: 1 })).toBeVisible();
    await expectRedIsSemantic(page);
  });
});

test.describe("quoted tool strings are monospace", () => {
  test("a finding's tool id renders in the mono face", async ({ page }) => {
    await page.goto(RESULTS);
    const name = page.getByText("weather/set_location").first();
    await expect(name).toBeVisible();

    const { actual, mono } = await name.evaluate((element) => ({
      actual: getComputedStyle(element).fontFamily,
      mono: getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim(),
    }));
    // The token's first family is the self-hosted face; `next/font` rewrites the variable, so
    // compare on the fallback the token and the element must share.
    expect(actual).toContain("JetBrains Mono");
    expect(mono.length).toBeGreaterThan(0);
  });
});

test.describe("the live region announces the stream", () => {
  test("exists before the first event and settles on a final message", async ({ page }) => {
    // Paused at the document so the region can be asserted before any verdict arrives.
    await page.route("**/api/v1/scans/*/events", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 400));
      await route.continue();
    });

    await page.goto(RESULTS);
    const region = page.getByTestId("stream-status");
    await expect(region).toBeAttached();
    await expect(region).toHaveAttribute("aria-live", "polite");

    await expect(region).toHaveText(
      `Triage complete: ${EXPECTED.confirmed} confirmed, ${EXPECTED.needsReview} needs review, ` +
        `${EXPECTED.dismissed} dismissed.`,
      { timeout: 20_000 },
    );
  });

  test("says the scan could not be read when both paths fail", async ({ page }) => {
    await mockFail(page, ["events", "findings"]);
    await page.goto(RESULTS);
    await expect(page.getByTestId("stream-status")).toHaveText("The scan could not be read.");
  });
});

test.describe("failures never read as results", () => {
  test("the results list says the scan could not be read, not that it was clean", async ({
    page,
  }) => {
    await mockFail(page, ["events", "findings"]);
    await page.goto(RESULTS);

    await expect(page.getByText("This scan could not be read.")).toBeVisible();
    await expect(page.getByText(/No findings to triage/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Try again" }).first()).toBeVisible();
  });

  test("a failed scan summary is reported above the list", async ({ page }) => {
    await mockFail(page, ["scan"]);
    await page.goto(RESULTS);
    await expect(page.getByText("This scan's summary could not be read.")).toBeVisible();
  });

  test("the benchmark distinguishes an outage from a benchmark nobody has run", async ({
    page,
  }) => {
    await mockFail(page, ["bench"]);
    await page.goto("/benchmark");
    await expect(page.getByText("The published numbers could not be loaded.")).toBeVisible();
    await expect(page.getByTestId("provisional-notice")).toHaveCount(0);
  });

  test("the landing hero reports a sample it could not read", async ({ page }) => {
    await mockFail(page, ["scan", "findings", "events"]);
    await page.goto("/");
    await expect(page.getByText("The sample scan could not be read.")).toBeVisible();
  });

  test("a deep-linked finding reports the failure", async ({ page }) => {
    await mockFail(page, ["findings"]);
    await page.goto(`${RESULTS}/finding/${encodeURIComponent("weather/set_location")}`);
    await expect(page.getByText("This finding could not be read.")).toBeVisible();
  });
});

test.describe("a clean scan", () => {
  test("says the scanner flagged nothing, with the real tool count", async ({ page }) => {
    await mockCleanScan(page);
    await page.goto(RESULTS);
    await expect(
      page.getByText(
        `No findings to triage. The scanner flagged nothing in these ${EXPECTED.toolCount} tools.`,
      ),
    ).toBeVisible();
  });
});
