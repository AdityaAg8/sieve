import { expect, type Page } from "@playwright/test";

import fixture from "../src/mocks/data/verdicts-sample-27.json";

const summary = (fixture as unknown as { summary: Record<string, unknown> }).summary;

/**
 * The numbers the E2E suite asserts are read out of the same fixture MSW serves, so a change to
 * the golden capture moves the tests with it rather than silently invalidating them. The golden
 * scanner flagged 4 of 27 tools — no test may assume 27 or 6.
 */
export const EXPECTED = {
  scanId: String(summary.id),
  toolCount: Number(summary.tool_count),
  scannerFlagged: Number(summary.scanner_flagged),
  confirmed: Number(summary.confirmed),
  needsReview: Number(summary.needs_review),
  dismissed: Number(summary.dismissed),
} as const;

/** The widths PRD §14.8 names. */
export const WIDTHS = [320, 375, 414, 768, 1280, 1920] as const;

export async function expectNoHorizontalScroll(page: Page, width: number): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, `horizontal overflow at ${width} px`).toBeLessThanOrEqual(0);
}
