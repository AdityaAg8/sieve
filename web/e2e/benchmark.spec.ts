import { expect, test } from "@playwright/test";

import { WIDTHS, expectNoHorizontalScroll, mockOverride } from "./helpers";

/** A summary shaped the way `make bench` will produce it. Every key is optional to the page. */
const MEASURED = {
  provisional: false,
  dataset_version: "sievebench@0.1",
  model_version: "sieve-deberta@0.1",
  generated_at: "2026-09-13T00:00:00Z",
  headline: {
    fpr_pipeline_real: { value: 0.07, lo: 0.03, hi: 0.13 },
    fpr_pipeline_all: { value: 0.09, lo: 0.05, hi: 0.15 },
    fp_reduction_vs_baseline: { value: 0.62, lo: 0.44, hi: 0.78 },
    mcnemar_p: 0.0031,
    holdout_recall: { value: 0.91, lo: 0.84, hi: 0.96 },
    real_poisoned_recall: { value: 0.83, lo: 0.66, hi: 0.94 },
    baseline_name: "cisco_yara@4.8.0",
  },
  tables: {
    ablations: [
      { id: 4, name: "Primary encoder, full serialisation", recall: { value: 0.9, lo: 0.8, hi: 0.95 } },
    ],
    per_tier: [
      { bucket: "easy", recall: 0.96, fpr: 0.04, n: 120 },
      { bucket: "medium", recall: 0.88, fpr: 0.07, n: 140 },
      { bucket: "hard", recall: 0.71, fpr: 0.11, n: 90 },
    ],
    per_concealment: [
      { bucket: "none", recall: 0.92, fpr: 0.05, n: 200 },
      { bucket: "html_comment", recall: 0.85, fpr: 0.06, n: 40 },
      { bucket: "zero_width", recall: 0.74, fpr: 0.08, n: 35 },
    ],
    confusion: {
      labels: ["benign", "imperative_benign", "poisoned"],
      counts: [
        [300, 8, 2],
        [6, 150, 9],
        [1, 4, 180],
      ],
      called_out: [1, 2],
    },
    agreement: {
      overall: 0.78,
      real_subset: 0.74,
      per_class: [
        { class: "benign", observed: 0.95, ac1: 0.93 },
        { class: "imperative_benign", observed: 0.82, ac1: 0.8 },
        { class: "poisoned", observed: 0.9, ac1: 0.88 },
      ],
      blind_vs_generator: 0.86,
    },
    minimal_pairs: [
      { tier: "easy", m11: 0.94, n: 50 },
      { tier: "hard", m11: 0.66, n: 30 },
    ],
    human_reference: [{ label: "Second annotator", recall: 0.93, fpr: 0.05 }],
    test_opened: [
      { git_sha: "0123456789abcdef", utc: "2026-09-12T10:00:00Z", rows: 180, reason: "v0.1 release" },
    ],
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
    expect(values.every((value) => value.startsWith("—"))).toBe(true);
  });

  test("says the holdout has not been opened when the log is empty", async ({ page }) => {
    await page.goto("/benchmark");
    await expect(page.getByText("Not yet opened.")).toBeVisible();
    await expect(page.getByTestId("holdout-log-row")).toHaveCount(0);
  });

  test("shows intervals as x.xx [lo, hi] once bench has run", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");
    await expect(page.getByTestId("provisional-notice")).toHaveCount(0);

    const headline = page.getByRole("region", { name: "Headline" });
    await expect(headline.getByText("0.07 [0.03, 0.13]")).toBeVisible();
    await expect(headline.getByText("0.62 [0.44, 0.78]")).toBeVisible();
    await expect(headline.getByText("McNemar p = 0.0031")).toBeVisible();
  });

  test("reports agreement three ways and never calls blind-vs-generator a kappa", async ({
    page,
  }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");

    const agreement = page.getByRole("region", { name: "Agreement" });
    await expect(agreement.getByText("κ, real subset")).toBeVisible();
    await expect(agreement.getByText("κ, overall")).toBeVisible();
    await expect(agreement.getByText("Blind-vs-generator agreement")).toBeVisible();
    await expect(agreement.getByText("not an inter-rater κ — the generator knew the answer")).toBeVisible();

    // Three per-class rows, each with observed agreement and AC1.
    await expect(agreement.locator("tbody tr")).toHaveCount(3);

    // The statistic's own name never carries a κ; only the caveat beneath it mentions one.
    const terms = await agreement.getByTestId("agreement-term").allTextContents();
    expect(terms).toContain("Blind-vs-generator agreement");
    expect(terms.filter((term) => term.includes("κ"))).toEqual(["κ, real subset", "κ, overall"]);
  });

  test("renders one holdout row per log line", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");
    await expect(page.getByTestId("holdout-log-row")).toHaveCount(
      MEASURED.tables.test_opened.length,
    );
    await expect(page.getByText("The holdout was opened this many times.")).toBeVisible();
  });

  test("has one bar per concealment value present in the data", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");

    const chart = page.getByTestId("per-concealment-chart");
    await expect(chart).toBeVisible();
    // Two series per bucket: Sieve recall and the false-positive rate.
    await expect(chart.locator(".recharts-bar-rectangle")).toHaveCount(
      MEASURED.tables.per_concealment.length * 2,
    );
    await expect(page.getByTestId("per-concealment-bucket")).toHaveCount(
      MEASURED.tables.per_concealment.length,
    );
  });

  test("calls out the imperative-benign cell in the confusion matrix", async ({ page }) => {
    await mockOverride(page, "bench", MEASURED);
    await page.goto("/benchmark");
    const matrix = page.getByRole("region", { name: "Confusion matrix" });
    await expect(
      matrix.getByText(/imperative-benign read as poisoned/).first(),
    ).toBeAttached();
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
