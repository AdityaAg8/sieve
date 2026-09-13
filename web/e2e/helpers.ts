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

/**
 * Merge a partial response over one of the MSW defaults for this page. Installed before the
 * first navigation, so the handler sees it on the very first request.
 */
export async function mockOverride(
  page: Page,
  name: "models" | "bench",
  value: Record<string, unknown>,
): Promise<void> {
  await page.addInitScript(
    ([key, json]) => window.localStorage.setItem(key as string, json as string),
    [`sieve.mock.${name}`, JSON.stringify(value)] as const,
  );
}

/** Routes the mock layer should answer with a 500, installed before the first navigation. */
export async function mockFail(page: Page, routes: string[]): Promise<void> {
  await page.addInitScript(
    (json) => window.localStorage.setItem("sieve.mock.fail", json as string),
    JSON.stringify(routes),
  );
}

/** Serve a scan the scanner flagged nothing in. */
export async function mockCleanScan(page: Page): Promise<void> {
  await page.addInitScript(() => window.localStorage.setItem("sieve.mock.clean", "1"));
}

/**
 * Red is semantic (PRD §14.2): confirmed findings, the sieve line, and destructive or error
 * states — never decoration, never "this tab is current". This walks every element on the page
 * (and its `::before` / `::after`), finds everything actually painting a `--red-*` token, and
 * fails unless each one sits inside an element that declares which of those roles it is.
 *
 * `data-red` is that declaration. The two exceptions it does not cover are named here: the
 * primary button, which PRD §14.2 gives `--red-fill` explicitly, and anything inside a
 * `role="alert"`, which is the error role.
 */
export async function expectRedIsSemantic(page: Page): Promise<void> {
  const offenders = await page.evaluate(() => {
    const probe = document.createElement("span");
    document.body.append(probe);
    const canonical = (value: string): string => {
      probe.style.color = "";
      probe.style.color = value;
      return getComputedStyle(probe).color;
    };
    const root = getComputedStyle(document.documentElement);
    const reds = new Set(
      ["--red-0", "--red-fill", "--red-1", "--red-wash"]
        .map((token) => root.getPropertyValue(token).trim())
        .filter(Boolean)
        .map(canonical),
    );
    probe.remove();

    const paintsRed = (style: CSSStyleDeclaration): boolean => {
      const painted: string[] = [style.color, style.backgroundColor, style.boxShadow, style.fill];
      for (const side of ["Top", "Right", "Bottom", "Left"] as const) {
        const width = parseFloat(
          style.getPropertyValue(`border-${side.toLowerCase()}-width`) || "0",
        );
        if (width > 0) painted.push(style.getPropertyValue(`border-${side.toLowerCase()}-color`));
      }
      return painted.some((value) => value && [...reds].some((red) => value.includes(red)));
    };

    const allowed = (element: Element): boolean =>
      element.closest('[data-red], .btn-primary, [role="alert"]') !== null;

    const out: string[] = [];
    for (const element of Array.from(document.querySelectorAll("*"))) {
      for (const pseudo of [null, "::before", "::after"]) {
        const style = getComputedStyle(element, pseudo ?? undefined);
        if (!paintsRed(style)) continue;
        if (allowed(element)) continue;
        out.push(
          `${element.tagName.toLowerCase()}${pseudo ?? ""} class="${String(element.className).slice(0, 60)}"`,
        );
      }
    }
    return [...new Set(out)];
  });

  expect(offenders, "elements painting --red-* without a semantic role").toEqual([]);
}

export async function expectNoHorizontalScroll(page: Page, width: number): Promise<void> {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, `horizontal overflow at ${width} px`).toBeLessThanOrEqual(0);
}
