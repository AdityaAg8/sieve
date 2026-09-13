import { expect, test, type Page } from "@playwright/test";

import { EXPECTED } from "./helpers";

/**
 * Everything the mouse can do, done with a keyboard, and a visible focus ring the whole way.
 * A 2 px `--focus` ring is a PRD §14.8 requirement, so it is asserted rather than assumed.
 */

async function focusOutline(page: Page): Promise<{ width: string; style: string }> {
  return page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    if (!active) return { width: "none", style: "none" };
    const style = getComputedStyle(active);
    return { width: style.outlineWidth, style: style.outlineStyle };
  });
}

test("tabbing from the top reaches the skip link, then the nav", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();

  const outline = await focusOutline(page);
  expect(outline.style).toBe("solid");
  expect(outline.width).toBe("2px");

  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Sieve", exact: true })).toBeFocused();
});

test("the landing actions are reachable and activate on Enter", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Try the sample" }).focus();
  const outline = await focusOutline(page);
  expect(outline.width).toBe("2px");

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/scan/${EXPECTED.scanId}$`));
});

test("a whole triage pass runs without a pointer", async ({ page }) => {
  await page.goto(`/scan/${EXPECTED.scanId}`);
  await expect(page.getByTestId("group-count-confirmed")).toBeVisible();

  // Filter, move, open, close — keys only.
  await page.keyboard.press("1");
  await expect(page.getByTestId("group-count-confirmed")).toHaveText(String(EXPECTED.confirmed));

  await page.keyboard.press("j");
  await page.keyboard.press("j");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: /^Finding / })).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: /^Finding / })).toHaveCount(0);

  await page.keyboard.press("0");
  await expect(page.getByTestId("group-count-dismissed")).toBeVisible();
});

test("the palette is operable with the keyboard alone", async ({ page }) => {
  await page.goto(`/scan/${EXPECTED.scanId}`);
  await expect(page.getByTestId("group-count-confirmed")).toBeVisible();

  await page.keyboard.press("ControlOrMeta+k");
  await page.keyboard.type("set_location");

  // cmdk re-ranks as it filters; wait for the tool to be the highlighted option before Enter.
  const option = page.getByRole("option", { name: /weather\/set_location/ });
  await expect(option).toHaveAttribute("data-selected", "true");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("dialog", { name: "Finding weather/set_location" })).toBeVisible();
});

test("the keyboard map is reachable and closes on Escape", async ({ page }) => {
  await page.goto(`/scan/${EXPECTED.scanId}`);
  await expect(page.getByTestId("group-count-confirmed")).toBeVisible();
  await page.keyboard.press("?");
  const map = page.getByRole("dialog", { name: "Keyboard map" });
  await expect(map).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(map).toHaveCount(0);
});

test("the scan form is completable with the keyboard", async ({ page }) => {
  await page.goto("/scan");
  await page.getByLabel("Tools JSON").focus();
  await page.keyboard.type('{"tools":[{"name":"a","inputSchema":{}}]}');
  await expect(page.getByRole("button", { name: "Triage 1 tool" })).toBeEnabled();
});

test("the docs navigation is reachable", async ({ page }) => {
  await page.goto("/docs");
  await page.getByRole("link", { name: "Quickstart" }).focus();
  const outline = await focusOutline(page);
  expect(outline.width).toBe("2px");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/docs\/quickstart$/);
});
