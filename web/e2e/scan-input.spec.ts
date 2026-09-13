import { expect, test } from "@playwright/test";

import { EXPECTED, WIDTHS, expectNoHorizontalScroll, mockFail, mockOverride } from "./helpers";

const VALID = JSON.stringify({
  tools: Array.from({ length: 27 }, (_, i) => ({
    name: `tool_${i}`,
    inputSchema: { type: "object" },
  })),
});

test.describe("scan input", () => {
  test("the primary control is disabled until something parses, then names the count", async ({
    page,
  }) => {
    await page.goto("/scan");
    const submit = page.getByRole("button", { name: /^Triage/ });
    await expect(submit).toBeDisabled();
    await expect(page.getByText("Paste a tools/list JSON to begin.")).toBeVisible();

    await page.getByLabel("Tools JSON").fill(VALID);
    await expect(submit).toBeEnabled();
    await expect(submit).toHaveText("Triage 27 tools");
  });

  test("a broken document is explained in plain language, with the line", async ({ page }) => {
    await page.goto("/scan");
    await page.getByLabel("Tools JSON").fill('{ "tools": [ { "name": "a" } ');
    const alert = page.locator("p[role=alert]");
    await expect(alert).toContainText("Could not parse this JSON.");
    await expect(alert).toContainText("Check line");
  });

  test("the remote tab stays hidden while the deployment does not report the flag", async ({
    page,
  }) => {
    await page.goto("/scan");
    await expect(page.getByRole("tab", { name: "Paste JSON" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Upload mcp.json" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Remote URL" })).toHaveCount(0);
  });

  test("the remote tab appears when the deployment reports the flag", async ({ page }) => {
    await mockOverride(page, "models", { remote_enabled: true });
    await page.goto("/scan");
    await expect(page.getByRole("tab", { name: "Remote URL" })).toBeVisible();
  });

  test("the tabs are operable from the keyboard", async ({ page }) => {
    await page.goto("/scan");
    await page.getByRole("tab", { name: "Paste JSON" }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(page.getByRole("tab", { name: "Upload mcp.json" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByLabel("Your mcp.json")).toBeVisible();
  });

  test("the sample opens without starting a scan", async ({ page }) => {
    await page.goto("/scan");
    await expect(page.getByRole("link", { name: "Open the sample scan" })).toBeVisible();
  });

  test("the sample's size is read from the scan, not typed into the copy", async ({ page }) => {
    await page.goto("/scan");
    await expect(page.getByTestId("sample-tool-count")).toHaveText(String(EXPECTED.toolCount));
    await expect(
      page.getByText(
        `The sample is ${EXPECTED.toolCount} tools from public MCP servers, triaged in advance`,
      ),
    ).toBeVisible();
  });

  test("the sample's size shows a dash until the scan answers", async ({ page }) => {
    await mockFail(page, ["scan"]);
    await page.goto("/scan");
    await expect(page.getByTestId("sample-tool-count")).toHaveText("—");
  });

  for (const width of WIDTHS) {
    test(`has no horizontal scroll at ${width} px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/scan");
      await expectNoHorizontalScroll(page, width);
    });
  }
});
