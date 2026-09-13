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
 * The origin the *server* renderer reaches the API on, or `null` when there is none.
 *
 * The browser's `apiBase()` is usually "" — same origin, resolved by whatever serves the app.
 * A server component has no origin to be relative to, so it needs an absolute one, and in a
 * container deployment it is not the public one: `API_INTERNAL_URL` is the service address.
 *
 * `null` means "do not render the scan on the server". That is the honest answer under
 * `NEXT_PUBLIC_API_MOCK=1`, where the only API is a service worker in a browser that has not
 * started yet, and it is why this returns a value rather than throwing: the page still renders,
 * it just fills in from the client the way lane L6 built it.
 */
export function serverApiBase(): string | null {
  if (mocksEnabled()) return null;
  const base = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_BASE || "";
  return base.startsWith("http") ? base.replace(/\/$/, "") : null;
}

/**
 * The pre-computed sample scan the landing page and the "Try the sample" control point at.
 * PRD §13.4: `make up` seeds this scan, so the id is a deployment fact, not a literal the
 * components are allowed to know.
 */
export function demoScanId(): string {
  return process.env.NEXT_PUBLIC_DEMO_SCAN_ID ?? "fixture-0000-0000-0000-000000000000";
}
