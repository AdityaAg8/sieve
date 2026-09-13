import { defineConfig, devices } from "@playwright/test";

/**
 * Two suites, two servers, and they are not interchangeable.
 *
 * `mocked` is lane L6's: a production build with `NEXT_PUBLIC_API_MOCK=1`, where MSW answers
 * every route from `fixtures/verdicts/sample-27.json`. It is the only way to exercise the states
 * a real API will not produce on demand — a 500 on `/findings`, a scan the scanner flagged
 * nothing in, a judge that is on — and it is what Lighthouse's mocked runs measure.
 *
 * `integration` is lane L9's: the real FastAPI app, a real Postgres, a real worker draining the
 * queue, and the browser's own `EventSource` on the real SSE route. Nothing in `e2e/integration`
 * may reach for a mock; the point of it is that the contract holds when nobody is pretending.
 *
 * The two run side by side, so each build gets its own `NEXT_DIST_DIR` — `next build` and
 * `next dev` both own `.next` and would otherwise overwrite one another.
 */
const MOCK_PORT = Number(process.env.PLAYWRIGHT_PORT ?? 3100);
const LIVE_PORT = Number(process.env.PLAYWRIGHT_LIVE_PORT ?? 3101);
const API_PORT = Number(process.env.E2E_API_PORT ?? 8000);

export const BASE_URL = `http://127.0.0.1:${MOCK_PORT}`;
export const LIVE_BASE_URL = `http://127.0.0.1:${LIVE_PORT}`;
export const API_BASE_URL = `http://127.0.0.1:${API_PORT}`;

/**
 * The scan `python -m sieve.db.seed` inserts, at the fixed id `uuid.UUID(int=0x5EED)`. The
 * integration suite reads it rather than a literal of its own, and `scripts/e2e-stack.mjs`
 * guarantees it exists before the first test runs.
 */
export const SEEDED_SCAN_ID = "00000000-0000-0000-0000-000000005eed";

/**
 * `compose` is the path lane L9's brief names and the one CI uses; `local` runs the same API and
 * worker straight from `uv` against an already-running Postgres, for a box with no Docker
 * daemon. `scripts/e2e-stack.mjs` is the same entry point either way.
 */
const STACK = process.env.E2E_STACK ?? "local";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    trace: "retain-on-failure",
    // Dark-only: the app has one theme, so the browser should ask for it.
    colorScheme: "dark",
  },
  projects: [
    {
      name: "mocked",
      testIgnore: /integration[\\/]/,
      use: { ...devices["Desktop Chrome"], baseURL: BASE_URL },
    },
    {
      name: "integration",
      testMatch: /integration[\\/].*\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], baseURL: LIVE_BASE_URL },
    },
  ],
  webServer: [
    {
      // A production build, which is the artefact Lighthouse measures on this path too.
      name: "web (mocked)",
      command: `npm run build && npm run start -- --port ${MOCK_PORT}`,
      url: BASE_URL,
      env: { NEXT_PUBLIC_API_MOCK: "1", NEXT_DIST_DIR: ".next-mock" },
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      // Ready means the database answered and the classifier loaded, not that a port is open.
      name: `api (${STACK})`,
      command: "node scripts/e2e-stack.mjs",
      url: `${API_BASE_URL}/api/v1/readyz`,
      env: { E2E_STACK: STACK, E2E_API_PORT: String(API_PORT), E2E_WEB_ORIGIN: LIVE_BASE_URL },
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      name: "web (live API)",
      command: `npm run dev -- --port ${LIVE_PORT}`,
      url: LIVE_BASE_URL,
      env: {
        NEXT_PUBLIC_API_MOCK: "0",
        NEXT_PUBLIC_API_BASE: API_BASE_URL,
        // The server renderer reads the API directly; see `src/lib/server-api.ts`.
        API_INTERNAL_URL: API_BASE_URL,
        NEXT_PUBLIC_DEMO_SCAN_ID: SEEDED_SCAN_ID,
        NEXT_DIST_DIR: ".next-live",
      },
      timeout: 240_000,
      reuseExistingServer: !process.env.CI,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
});
