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
   * `src/app/layout.tsx` reads `--bg-0` out of `src/styles/tokens.css` for the `theme-color`
   * meta (see `src/styles/tokens.server.ts`). The read is a runtime `fs` call against a path
   * built from `process.cwd()`, which the tracer cannot follow, so a deployment that ships only
   * traced files would start without the stylesheet and the layout would throw. Name it.
   */
  outputFileTracingIncludes: {
    "/*": ["src/styles/tokens.css"],
  },
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
