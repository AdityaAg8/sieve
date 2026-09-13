import createMDX from "@next/mdx";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  pageExtensions: ["ts", "tsx", "mdx"],
  /**
   * The E2E suite runs two builds of this app at once — one against MSW, one against the real
   * API — and `next build` and `next dev` both own `.next`. A build directory per server keeps
   * them from overwriting each other. Unset everywhere else, so the default stays `.next`.
   */
  distDir: process.env.NEXT_DIST_DIR || ".next",
  /**
   * `next dev` only serves its own dev resources to `localhost` unless told otherwise, and the
   * E2E suite drives `127.0.0.1` — Playwright's `webServer` waits on a literal address, not a
   * name. Without this the dev server blocks `/_next/*` for that origin and the page renders
   * with no JavaScript at all. Development only; a production build serves nothing of the kind.
   */
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  env: {
    /**
     * Off. The app talks to the real API (lane L9); MSW is a test dependency now, kept for the
     * unit suite and for the mocked E2E project, and `scripts/check-no-msw.mjs` proves it is
     * absent from a production bundle. The value is inlined at build time, which is what lets
     * the bundler delete the `import("@/mocks/browser")` branch in `src/app/providers.tsx`
     * rather than shipping a `msw` chunk nobody loads.
     */
    NEXT_PUBLIC_API_MOCK: process.env.NEXT_PUBLIC_API_MOCK ?? "0",
  },
};

export default createMDX()(nextConfig);
