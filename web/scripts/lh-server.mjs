/**
 * Starts `next start` for Lighthouse with the API environment set, and stays in the foreground
 * so `lhci` owns its lifetime. Referenced by `lighthouserc.json`'s `startServerCommand`.
 *
 * Without this the server renderer has no API address, `src/lib/server-api.ts` returns
 * `unavailable`, and the scan URL Lighthouse measures renders as a page with no list on it —
 * which is exactly the route whose cumulative layout shift the budget is about. An inline
 * `FOO=bar npm run start` in the JSON would do it on a POSIX shell and silently not on Windows.
 *
 * `NEXT_PUBLIC_API_BASE` is a *build-time* value and cannot be set here: the build that `lhci`
 * measures has to be made with it. See docs/handoffs/L9.md.
 */
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = resolve(dirname(fileURLToPath(import.meta.url)), "..");

const PORT = process.env.LH_PORT ?? "3200";
const API = process.env.LH_API_URL ?? "http://127.0.0.1:8000";
const SCAN = process.env.LH_SCAN_ID ?? "00000000-0000-0000-0000-000000005eed";

const child = spawn("npx", ["next", "start", "--port", PORT], {
  cwd: WEB,
  env: {
    ...process.env,
    NEXT_PUBLIC_API_MOCK: "0",
    // What the *server* renderer reads. `lighthouserc.json`'s scan URL is meaningless without it.
    API_INTERNAL_URL: API,
    NEXT_PUBLIC_DEMO_SCAN_ID: SCAN,
  },
  shell: true,
  stdio: "inherit",
});

const stop = () => {
  if (child.pid === undefined) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
};

for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) process.on(signal, () => stop());
child.on("exit", (code) => process.exit(code ?? 0));
