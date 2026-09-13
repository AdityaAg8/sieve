import { expect, test, type Page } from "@playwright/test";

import { EXPECTED, WIDTHS, expectNoHorizontalScroll } from "./helpers";

async function heroSentence(page: Page): Promise<string> {
  const heading = page.getByRole("heading", { level: 1 });
  await expect(heading).toContainText("Sieve confirmed");
  return ((await heading.textContent()) ?? "").replace(/\s+/g, " ").trim();
}

test.describe("landing page", () => {
  test("the hero states the scanner's count and Sieve's, both from the scan data", async ({
    page,
  }) => {
    await page.goto("/");
    expect(await heroSentence(page)).toBe(
      `The scanner flagged ${EXPECTED.scannerFlagged}. Sieve confirmed ${EXPECTED.confirmed}.`,
    );
  });

  test("the sieve's trays hold every tool the scan triaged", async ({ page }) => {
    await page.goto("/");
    const figure = page.getByRole("figure", { name: "The sieve" });
    await expect(figure).toBeVisible();
    await expect(page.getByTestId("sieve-tray-count-confirmed")).toHaveText(
      String(EXPECTED.confirmed),
    );
    await expect(page.getByTestId("sieve-tray-count-needs_review")).toHaveText(
      String(EXPECTED.needsReview),
    );
    await expect(page.getByTestId("sieve-tray-count-dismissed")).toHaveText(
      String(EXPECTED.dismissed),
    );
  });

  test("'Try the sample' opens the sample scan", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Try the sample" }).click();
    await expect(page).toHaveURL(new RegExp(`/scan/${EXPECTED.scanId}$`));
  });

  test("'Paste your tools' opens the scan input", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Paste your tools" }).click();
    await expect(page).toHaveURL(/\/scan$/);
  });

  test("the proof block shows a dash and 'metric to confirm' while bench is provisional", async ({
    page,
  }) => {
    await page.goto("/");
    const proof = page.getByRole("region", { name: /what the numbers say/i });
    await expect(proof).toContainText("metric to confirm");
    await expect(proof.getByText("—").first()).toBeVisible();
  });

  test.describe("with reduced motion", () => {
    test.use({ reducedMotion: "reduce" });

    test("nothing is still animating a moment after load", async ({ page }) => {
      await page.goto("/");
      await page.getByRole("figure", { name: "The sieve" }).waitFor();
      await page.waitForTimeout(100);
      const running = await page.evaluate(
        () => document.getAnimations().filter((a) => a.playState === "running").length,
      );
      expect(running).toBe(0);
      await expect(page.getByRole("figure", { name: "The sieve" })).toHaveAttribute(
        "data-motion",
        "static",
      );
    });
  });

  for (const width of WIDTHS) {
    test(`has no horizontal scroll at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      await expectNoHorizontalScroll(page, width);
    });
  }
});
