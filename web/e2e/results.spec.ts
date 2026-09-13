import { expect, test } from "@playwright/test";

import { EXPECTED, WIDTHS, expectNoHorizontalScroll, mockOverride } from "./helpers";

const RESULTS = `/scan/${EXPECTED.scanId}`;
const JUDGE_OFF_NOTICE =
  "The judge is disabled on this deployment; band findings are reported as needs review.";

test.describe("results screen", () => {
  test("fills in as the mocked stream arrives and settles on the real counts", async ({ page }) => {
    await page.goto(RESULTS);

    await expect(page.getByTestId("verdict-bar-count-confirmed")).toHaveText(
      String(EXPECTED.confirmed),
    );
    await expect(page.getByTestId("verdict-bar-count-needs_review")).toHaveText(
      String(EXPECTED.needsReview),
    );
    await expect(page.getByTestId("verdict-bar-count-dismissed")).toHaveText(
      String(EXPECTED.dismissed),
    );

    await expect(
      page.getByText(
        `The scanner flagged ${EXPECTED.scannerFlagged}. Sieve confirmed ${EXPECTED.confirmed}.`,
      ),
    ).toBeVisible();
  });

  test("groups confirmed first", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toHaveText(String(EXPECTED.confirmed));
    const headings = await page.getByRole("heading", { level: 2 }).allTextContents();
    const order = headings.filter((h) =>
      /Confirmed|Needs review|Dismissed/.test(h),
    );
    expect(order[0]).toContain("Confirmed");
  });

  test("j then Enter opens the first finding", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toBeVisible();

    await page.keyboard.press("j");
    await page.keyboard.press("Enter");

    const drawer = page.getByRole("dialog", { name: /^Finding / });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText("Scanner said")).toBeVisible();
    await expect(drawer.getByText("Sieve said")).toBeVisible();
  });

  test("2 filters to needs review", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toBeVisible();

    await page.keyboard.press("2");
    await expect(page.getByTestId("group-count-needs_review")).toHaveText(
      String(EXPECTED.needsReview),
    );
    await expect(page.getByTestId("group-count-confirmed")).toHaveCount(0);
  });

  test("? shows the keyboard map", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toBeVisible();
    await page.keyboard.press("?");
    await expect(page.getByRole("dialog", { name: "Keyboard map" })).toBeVisible();
  });

  test("the palette opens with the meta key and filters to a tool", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toBeVisible();

    await page.keyboard.press("ControlOrMeta+k");
    const input = page.getByPlaceholder("Search tools, or type a command");
    await expect(input).toBeVisible();

    await input.fill("set_location");
    const option = page.getByRole("option", { name: /weather\/set_location/ });
    await expect(option).toBeVisible();
    await option.click();

    await expect(page.getByRole("dialog", { name: "Finding weather/set_location" })).toBeVisible();
  });

  test("e exports a SARIF 2.1.0 document", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toBeVisible();

    const download = page.waitForEvent("download");
    await page.keyboard.press("e");
    const file = await download;
    const stream = await file.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    const sarif = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    expect(sarif.version).toBe("2.1.0");
    expect(Array.isArray(sarif.runs)).toBe(true);
  });

  test("a dismissal states its class and its computed probability", async ({ page }) => {
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-dismissed")).toBeVisible();
    await expect(
      page.getByText(/^Dismissed — descriptive documentation \(p = 0\.\d{2}\)$/).first(),
    ).toBeVisible();
    await expect(
      page.getByText(/^Dismissed — legitimate imperative instruction \(p = 0\.\d{2}\)$/).first(),
    ).toBeVisible();
  });

  test("the judge-off notice is shown when the deployment reports judge_enabled false", async ({
    page,
  }) => {
    await mockOverride(page, "models", { judge_enabled: false });
    await page.goto(RESULTS);
    await expect(page.getByText(JUDGE_OFF_NOTICE)).toBeVisible();
  });

  test("the judge-off notice is absent when the judge is enabled", async ({ page }) => {
    await mockOverride(page, "models", { judge_enabled: true });
    await page.goto(RESULTS);
    await expect(page.getByTestId("group-count-confirmed")).toBeVisible();
    await expect(page.getByText(JUDGE_OFF_NOTICE)).toHaveCount(0);
  });

  for (const width of WIDTHS) {
    test(`has no horizontal scroll at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(RESULTS);
      await expect(page.getByTestId("group-count-confirmed")).toBeVisible();
      await expectNoHorizontalScroll(page, width);
    });
  }
});
