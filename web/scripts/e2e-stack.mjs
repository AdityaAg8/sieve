/**
 * Brings up the API the real-API E2E project talks to, and stays in the foreground so Playwright
 * owns its lifetime. Started from `playwright.config.ts` as a `webServer` entry whose `url` is
 * `/api/v1/readyz`, so "ready" means the database answered *and* the classifier loaded (PRD
 * §13.1) — not merely that a port is open.
 *
 * Two paths, chosen with `E2E_STACK`:
 *
 *   compose  `docker compose up -d --build`, then seed. This is the path lane L9's brief names
 *            and the one CI uses.
 *   local    (default) migrate and seed, then run uvicorn and the worker straight from `uv`.
 *            The same processes compose would run, on a box with no Docker daemon.
 *
 * Neither path is a test double: both serve `sieve.api.main:app` against a real Postgres with a
 * real worker draining the queue. What differs is who starts Postgres.
 *
 * The judge is off and `ANTHROPIC_API_KEY` is removed from the child environment, so the shared
 * `FakeJudge` runs and no test can spend money (PRD §13.2's public-demo posture, which is also
 * what makes the judge-off notice on the results page a real assertion rather than a mock).
 */
import { spawn, spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const MODE = process.env.E2E_STACK ?? "local";

const API_PORT = process.env.E2E_API_PORT ?? "8000";
const WEB_ORIGIN = process.env.E2E_WEB_ORIGIN ?? "http://127.0.0.1:3101";
const DATABASE_URL =
  process.env.E2E_DATABASE_URL ?? "postgresql+psycopg://sieve:sieve@127.0.0.1:5433/sieve";

/** Settings every child is pinned to, so no test outcome depends on a stray shell variable. */
const FIXED = {
  PYTHONUTF8: "1",
  SIEVE_MODEL_ID: "fixture",
  // The public demo runs judge-off (PRD §13.2); the E2E asserts the notice that follows.
  SIEVE_JUDGE_ENABLED: "false",
  // A suite must not be refused by the quota an earlier run from the same box spent.
  SIEVE_DAILY_SCANS_PER_IP: process.env.SIEVE_DAILY_SCANS_PER_IP ?? "1000",
};

function childEnv() {
  const env = { ...process.env, ...FIXED, DATABASE_URL, WEB_ORIGIN };
  // No key, no `AnthropicJudge`: `sieve/api/deps.py` falls back to the shared `FakeJudge`.
  delete env.ANTHROPIC_API_KEY;
  return env;
}

const children = [];

function run(label, command, args) {
  const child = spawn(command, args, {
    cwd: ROOT,
    env: childEnv(),
    shell: true,
    // Its own process group, so one signal reaches the shell and the process it started.
    detached: process.platform !== "win32",
    stdio: ["ignore", "pipe", "pipe"],
  });
  const say = (stream) => (data) =>
    process[stream].write(`[${label}] ${String(data).trimEnd()}\n`);
  child.stdout.on("data", say("stdout"));
  child.stderr.on("data", say("stderr"));
  child.on("exit", (code) => {
    console.error(`[${label}] exited with ${code}`);
    // A stack missing half of itself would fail every test with a timeout instead of a reason.
    if (!stopping) shutdown(code ?? 1);
  });
  children.push({ label, child });
  return child;
}

function step(label, command, args) {
  console.log(`[${label}] ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: ROOT,
    env: childEnv(),
    shell: true,
    stdio: "inherit",
  });
  if (result.status !== 0) {
    console.error(`[${label}] failed with ${result.status}`);
    process.exit(result.status ?? 1);
  }
}

let stopping = false;

/**
 * `shell: true` means the child is a shell that owns the real process. On Windows only
 * `taskkill /T` reaches through it; elsewhere the process group does.
 */
function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const { child } of children) {
    if (child.pid === undefined) continue;
    try {
      if (process.platform === "win32") {
        spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
      } else {
        process.kill(-child.pid, "SIGTERM");
      }
    } catch {
      // Already gone. Nothing to do, and nothing worth failing the run over.
    }
  }
  if (MODE === "compose") {
    spawnSync("docker", ["compose", "down"], { cwd: ROOT, stdio: "inherit", shell: true });
  }
  process.exit(code);
}

for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => shutdown(0));
}

if (MODE === "compose") {
  step("compose", "docker", ["compose", "up", "-d", "--build"]);
  step("seed", "uv", ["run", "python", "-m", "sieve.db.seed"]);
  console.log("[stack] compose is up; the API is served by the compose stack");
} else {
  // Migrates to head and inserts the sample scan at the fixed id, idempotently.
  step("seed", "uv", ["run", "python", "-m", "sieve.db.seed"]);
  run("api", "uv", [
    "run",
    "uvicorn",
    "sieve.api.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    API_PORT,
    // The same flag `make serve` uses, and on Windows it is load-bearing rather than a
    // convenience: uvicorn ≥ 0.36 picks its event loop from a factory, not from the policy
    // `sieve.db.session.use_selector_event_loop_on_windows()` installs, and that factory
    // returns a `ProactorEventLoop` — which psycopg refuses to run async on. Reload mode runs
    // the server in a subprocess, and that path asks the factory for a `SelectorEventLoop`.
    "--reload",
    // Watch the API, not this repository: an edit under `web/` must not restart the API
    // in the middle of a test.
    "--reload-dir",
    "sieve",
  ]);
  run("worker", "uv", ["run", "python", "-m", "sieve.worker.main"]);
}

// Playwright waits on the `url`; this process only has to stay alive until it is killed.
setInterval(() => {}, 1 << 30);
