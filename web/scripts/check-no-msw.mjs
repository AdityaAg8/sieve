/**
 * Fails if a production build ships Mock Service Worker.
 *
 * Two halves, and both have bitten this app:
 *
 * 1. **The client bundle.** `msw` is a dev dependency. The app imports it from exactly one place
 *    — the dynamic `import("@/mocks/browser")` in `src/app/providers.tsx` — behind a comparison
 *    against the build-time literal `NEXT_PUBLIC_API_MOCK`, so a production build folds the
 *    condition to `false` and deletes the import with it. Nothing about that is guaranteed by the
 *    type system: refactor the check into a helper function and the bundler can no longer fold
 *    it, the chunk comes back, and `npm ci --omit=dev` then produces a build that cannot resolve
 *    its own import.
 * 2. **`public/mockServiceWorker.js`.** Everything in `public/` is served by the deployment, so a
 *    service worker left there is shipped to real users whatever the bundle says.
 *    `scripts/mock-worker.mjs` puts it there for a mocked build and takes it away otherwise; this
 *    asserts the result.
 *
 * Run from `postbuild`, so `npm run build` — in `make lint` and in CI's `web` job — checks it. A
 * mocked build (`NEXT_PUBLIC_API_MOCK=1`) is skipped: MSW is supposed to be in that one.
 *
 * Usage: `node scripts/check-no-msw.mjs [distDir]` after `next build`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

if (process.env.NEXT_PUBLIC_API_MOCK === "1") {
  console.log("mocked build: MSW is expected here, nothing to check");
  process.exit(0);
}

const dist = resolve(process.argv[2] ?? process.env.NEXT_DIST_DIR ?? ".next");
const chunks = join(dist, "static", "chunks");
const served = fileURLToPath(new URL("../public/mockServiceWorker.js", import.meta.url));

/** Strings that only a bundled MSW (or our own start-up call) can put in a client chunk. */
const MARKERS = ["mockServiceWorker.js", "[MSW]", "setupWorker", "msw/browser"];

function* files(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    return; // a build with no client chunks has nothing to leak
  }
  for (const entry of entries) {
    const path = join(dir, entry);
    let stat;
    try {
      stat = statSync(path);
    } catch {
      continue; // a dangling link is not a chunk
    }
    if (stat.isDirectory()) yield* files(path);
    else if (path.endsWith(".js")) yield path;
  }
}

const offenders = [];

for (const path of files(chunks)) {
  const source = readFileSync(path, "utf8");
  const found = MARKERS.filter((marker) => source.includes(marker));
  if (found.length > 0) offenders.push(`${path}: ${found.join(", ")}`);
}

let servedWorker = false;
try {
  statSync(served);
  servedWorker = true;
} catch {
  // absent, which is the point
}

if (offenders.length > 0 || servedWorker) {
  if (offenders.length > 0) {
    console.error(
      `Mock Service Worker reached the production bundle in ${dist}:\n  ${offenders.join("\n  ")}\n` +
        'Keep the import behind a literal `process.env.NEXT_PUBLIC_API_MOCK === "1"` check.',
    );
  }
  if (servedWorker) {
    console.error(
      `public/mockServiceWorker.js exists and would be served to real users.\n` +
        "`node scripts/mock-worker.mjs` removes it when NEXT_PUBLIC_API_MOCK is not 1; it runs " +
        "from `prebuild`, so a build that skipped the lifecycle scripts is the likely cause.",
    );
  }
  process.exit(1);
}

console.log(`no MSW in ${chunks}, none in public/`);
