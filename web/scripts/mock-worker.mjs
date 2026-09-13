/**
 * Puts Mock Service Worker's service worker into `public/` for a mocked build, and takes it out
 * again for every other build.
 *
 * A service worker has to be a real file at a real path — `public/mockServiceWorker.js` is the
 * only thing MSW's browser integration can register — and anything in `public/` is served by the
 * deployment. So the file is *not* kept there: `msw`'s `workerDirectory` writes it to `mocks/`
 * on install, and this script copies it across only when `NEXT_PUBLIC_API_MOCK=1`.
 *
 * `scripts/check-no-msw.mjs` asserts the other half — that a production build leaves `public/`
 * without it. Run from `pre{dev,build,e2e}`; safe to run any number of times.
 */
import { copyFileSync, existsSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));

export const SOURCE = here("../mocks/mockServiceWorker.js");
export const SERVED = here("../public/mockServiceWorker.js");

const wanted = process.env.NEXT_PUBLIC_API_MOCK === "1";

if (wanted) {
  if (!existsSync(SOURCE)) {
    console.error(
      "mock worker: mocks/mockServiceWorker.js is missing. `npm ci` regenerates it " +
        '(package.json "msw": { "workerDirectory": ["mocks"] }).',
    );
    process.exit(1);
  }
  copyFileSync(SOURCE, SERVED);
  console.log("mock worker → public/mockServiceWorker.js");
} else if (existsSync(SERVED)) {
  rmSync(SERVED);
  console.log("mock worker ✗ removed from public/ (NEXT_PUBLIC_API_MOCK is not 1)");
}
