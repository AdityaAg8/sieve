/** Runtime configuration. Every value is a build-time `NEXT_PUBLIC_*` with an explicit default. */

/** Origin the API lives on. Empty string means "same origin", which is what the deploy uses. */
export function apiBase(): string {
  return process.env.NEXT_PUBLIC_API_BASE ?? "";
}

/** True when MSW should intercept requests in the browser (`NEXT_PUBLIC_API_MOCK=1`). */
export function mocksEnabled(): boolean {
  return process.env.NEXT_PUBLIC_API_MOCK === "1";
}

/**
 * The pre-computed sample scan the landing page and the "Try the sample" control point at.
 * PRD §13.4: `make up` seeds this scan, so the id is a deployment fact, not a literal the
 * components are allowed to know.
 */
export function demoScanId(): string {
  return process.env.NEXT_PUBLIC_DEMO_SCAN_ID ?? "fixture-0000-0000-0000-000000000000";
}
