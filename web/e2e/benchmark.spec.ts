import { expect, test } from "@playwright/test";

import type { BenchSummary } from "../src/lib/api";
import { formatInterval, headlineOf, tablesOf } from "../src/lib/bench";
import fixture from "../src/mocks/fixtures/bench-summary.json";

import { WIDTHS, expectNoHorizontalScroll, mockOverride } from "./helpers";

/**
 * `GET /bench/summary` over results files written by their real producers (H3, H5, F3), as
 * `python -m tests.api.bench_fixture` regenerates it. Fixture numbers, not measurements: the
 * page is checked for reading the producers' keys, never for what the numbers are.
 */
const MEASURED = fixture as unknown as BenchSummary & Record<string, unknown>;
const TABLES = tablesOf(MEASURED);
const HEADLINE = headlineOf(MEASURED);

/** The fixture plus a confusion matrix, which no v1 producer writes, for the call-out test. */
const WITH_CONFUSION = {
  ...MEASURED,
  tables: {
    ...MEASURED.tables,
    confusion: {
      labels: ["benign", "imperative_benign", "poisoned"],
      counts: [
        [300, 8, 2],
        [6, 150, 9],
        [1, 4, 180],
      ],
      called_out: [1, 2],
    },
  },
};

test.describe("benchmark page", () => {
  test("shows a dash everywhere and one notice while bench is provisional", async ({ page }) => {
    await page.goto("/benchmark");
    await expect(page.getByTestId("provisional-notice")).toHaveCount(1);
    await expect(page.getByTestId("provisional-notice")).toContainText("make bench");

    const headline = page.getByRole("region", { name: "Headline" });
    const values = await headline.locator("tbody tr td:nth-child(3)").allTextContents();
    expect(values.length).toBeGreaterThan(0);
    // M1 is the one row that states a fact rather than a measurement: the judge is deferred, so
    // the pipeline's false-positive rate is not measured, with or without a bench run.
    const measuredRows = values.filter((value) => !value.startsWith("not measured"));
    expect(values.length - measuredRows.length).toBe(1);
    expect(measuredRows.every((value) => value.startsWith("—"))).toBe(true);
  });

  test("says the holdout has not been opened when the log is empty", async ({ page }) => {
    await page.goto("/benchmark");
    await expect(page.getByText("Not yet opened.")).toBeVisible();
    await expect(page.getByTestId("holdout-log-row")).toHaveCount(0);
  });

  test("reads the sealed report's keys once bench has run", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");
    await expect(page.getByTestId("provisional-notice")).toHaveCount(0);

    const headline = page.getByRole("region", { name: "Headline" });
    // The table renders once per breakpoint; assert on the copy this viewport shows.
    const shown = (text: string) => headline.getByText(text).filter({ visible: true }).first();
    await expect(shown("Real tools sent to review")).toBeVisible();
    await expect(shown(formatInterval(HEADLINE.real_review_share))).toBeVisible();
    await expect(shown("not measured: judge deferred (v1.1)")).toBeVisible();
    await expect(shown("Synthetic attacks caught")).toBeVisible();
    await expect(
      headline.getByText(
        HEADLINE.release_gate_passed ? "Release gate passed" : "Release gate not met",
      ),
    ).toBeVisible();
  });

  test("shows the deferred judge rows as deferred, never as numbers", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");

    await expect(page.getByTestId("ablation-row")).toHaveCount(TABLES.ablations?.length ?? 0);
    const deferred = page.locator('[data-testid="ablation-row"][data-status="deferred"]');
    await expect(deferred).toHaveCount(3);
    for (const text of await deferred.allTextContents()) {
      expect(text).toContain("Deferred");
      expect(text).toContain("D8");
      expect(text).not.toMatch(/\b0\.\d\d\b/);
    }
  });

  test("labels agreement as human vs ensemble and never calls checker agreement a kappa", async ({
    page,
  }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");

    const agreement = page.getByRole("region", { name: "Agreement" });
    await expect(agreement.getByText("κ, human vs ensemble")).toBeVisible();
    await expect(
      agreement.getByText("human vs ensemble, 104-row sealed audit", { exact: true }),
    ).toBeVisible();
    await expect(agreement.getByText("Checker-vs-generator agreement")).toBeVisible();
    await expect(agreement.getByText(/second annotator/i)).toHaveCount(0);

    // The statistic's own name never carries a κ; only the caveat beneath it mentions one.
    const terms = await agreement.getByTestId("agreement-term").allTextContents();
    expect(terms.filter((term) => term.includes("κ"))).toEqual([
      "κ, human vs ensemble",
      "κ, dev-50 gate",
    ]);

    const reference = page.getByRole("region", { name: "Human reference point" });
    await expect(
      reference.getByText("human vs ensemble, 104-row sealed audit, report-only"),
    ).toBeVisible();
  });

  test("renders one holdout row per log line", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");
    await expect(page.getByTestId("holdout-log-row")).toHaveCount(TABLES.test_opened?.length ?? 0);
    await expect(page.getByText("The holdout was opened this many times.")).toBeVisible();
  });

  test("has one bucket per concealment value in the sealed report", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");

    await expect(page.getByTestId("per-concealment-chart")).toBeVisible();
    await expect(page.getByTestId("per-concealment-bucket")).toHaveCount(
      Object.keys(TABLES.holdout?.per_concealment ?? {}).length,
    );
  });

  test("calls out the imperative-benign cell in the confusion matrix", async ({ page }) => {
    await mockOverride(page, "bench", WITH_CONFUSION);
    await page.goto("/benchmark");
    const matrix = page.getByRole("region", { name: "Confusion matrix" });
    await expect(matrix.getByText(/imperative-benign read as poisoned/).first()).toBeAttached();
  });

  for (const width of WIDTHS) {
    test(`has no horizontal scroll at ${width} px`, async ({ page }) => {
      await mockOverride(page, "bench", MEASURED);
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/benchmark");
      await expect(page.getByRole("region", { name: "Headline" })).toBeVisible();
      await expectNoHorizontalScroll(page, width);
    });
  }
});

test.describe("docs", () => {
  test("lists every page and opens one", async ({ page }) => {
    await page.goto("/docs");
    await expect(page.getByRole("link", { name: "Quickstart" })).toBeVisible();
    await page.getByRole("link", { name: "Limitations" }).click();
    await expect(page).toHaveURL(/\/docs\/limitations$/);
    await expect(page.getByRole("heading", { name: "Limitations", level: 1 })).toBeVisible();
  });
});
