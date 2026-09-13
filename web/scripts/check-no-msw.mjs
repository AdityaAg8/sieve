/**
 * Fails if a production build ships Mock Service Worker.
 *
 * `msw` is a dev dependency. The app imports it from exactly one place — the dynamic
 * `import("@/mocks/browser")` in `src/app/providers.tsx` — behind a comparison against the
 * build-time literal `NEXT_PUBLIC_API_MOCK`, so a production build folds the condition to
 * `false` and deletes the import with it. Nothing about that is guaranteed by the type system:
 * refactor the check into a helper function and the bundler can no longer fold it, the chunk
 * comes back, and `npm ci --omit=dev` then produces a build that cannot resolve its own import.
 * This script is the guard for that, and it costs one directory walk.
 *
 * Usage: `node scripts/check-no-msw.mjs [distDir]` after `next build`.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const dist = resolve(process.argv[2] ?? process.env.NEXT_DIST_DIR ?? ".next");
const chunks = join(dist, "static", "chunks");

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
    if (statSync(path).isDirectory()) yield* files(path);
    else if (path.endsWith(".js")) yield path;
  }
}

const offenders = [];
for (const path of files(chunks)) {
  const source = readFileSync(path, "utf8");
  const found = MARKERS.filter((marker) => source.includes(marker));
  if (found.length > 0) offenders.push(`${path}: ${found.join(", ")}`);
}

if (offenders.length > 0) {
  console.error(
    `Mock Service Worker reached the production bundle in ${dist}:\n  ${offenders.join("\n  ")}\n` +
      "Keep the import behind a literal `process.env.NEXT_PUBLIC_API_MOCK === \"1\"` check.",
  );
  process.exit(1);
}

console.log(`no MSW in ${chunks}`);
