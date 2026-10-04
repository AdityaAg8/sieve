import { expect, test } from "@playwright/test";

import measured from "../src/mocks/fixtures/bench-summary.v1.json";

import { EXPECTED, WIDTHS, expectNoHorizontalScroll, mockOverride } from "./helpers";

/**
 * Full-page screenshots at the six widths PRD §14.8 names, committed to `web/screenshots/` for
 * human review, plus the assertion that matters more than the pictures: nothing scrolls
 * sideways at any of them.
 *
 * Reduced motion is on for every shot so the pages are captured in their settled state — a
 * screenshot of a tile mid-fall tells a reviewer nothing.
 */
type Route = {
  name: string;
  path: string;
  ready: "figure" | "tab" | "heading" | "region";
  readyName: string;
  /** Extra wait for a page that fills in after first paint. */
  settled?: (page: import("@playwright/test").Page) => Promise<void>;
  /** A benchmark summary to serve instead of the provisional default. */
  bench?: Record<string, unknown>;
};

const ROUTES: Route[] = [
  { name: "landing", path: "/", ready: "figure" as const, readyName: "The sieve" },
  { name: "scan", path: "/scan", ready: "tab" as const, readyName: "Paste JSON" },
  {
    name: "results",
    path: `/scan/${EXPECTED.scanId}`,
    ready: "heading" as const,
    readyName: "Confirmed",
    // The results page fills in over ~29 mocked events; capture it settled, not mid-stream.
    settled: async (page: import("@playwright/test").Page) =>
      expect(page.getByTestId("group-count-dismissed")).toHaveText(String(EXPECTED.dismissed)),
  },
  {
    // The measured v1 results (`python -m tests.api.bench_fixture --real`, training plan §13
    // step 14): the summary the API serves over the committed `bench/results/`.
    name: "benchmark",
    path: "/benchmark",
    ready: "region" as const,
    readyName: "Headline",
    bench: measured as unknown as Record<string, unknown>,
    settled: async (page: import("@playwright/test").Page) =>
      expect(page.getByTestId("provisional-notice")).toHaveCount(0),
  },
];

test.use({ reducedMotion: "reduce" });

for (const route of ROUTES) {
  for (const width of WIDTHS) {
    test(`${route.name} at ${width} px`, async ({ page }) => {
      if (route.bench) await mockOverride(page, "bench", route.bench);
      await page.setViewportSize({ width, height: 900 });
      await page.goto(route.path);
      await expect(page.getByRole(route.ready, { name: route.readyName }).first()).toBeVisible();
      await route.settled?.(page);

      await expectNoHorizontalScroll(page, width);

      await page.screenshot({
        path: `screenshots/${route.name}-${width}.png`,
        fullPage: true,
        animations: "disabled",
      });
    });
  }
}
