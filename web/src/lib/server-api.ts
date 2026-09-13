import type { Finding, FindingPage, ScanSummary } from "./api";
import { serverApiBase } from "./config";

/**
 * What the server renderer managed to learn about a scan before it sent the first byte.
 *
 * `unavailable` is not a failure: it is the deployment saying there is no API the *server* can
 * reach — the mocked build, where the only API is a service worker. The page then fills from the
 * client, exactly as lane L6 built it.
 */
export type ScanSnapshot =
  | { state: "unavailable" }
  | { state: "ok"; scan: ScanSummary; findings: Finding[] }
  | { state: "missing" }
  | { state: "error" };

/** A server render must not hang on a slow API; the client can always finish the job. */
const TIMEOUT_MS = 3000;

type Result<T> = { ok: true; body: T } | { ok: false; status: number };

/**
 * The deadline is a race rather than an `AbortSignal`, because the loser is harmless — a request
 * this render has stopped waiting for — and because a signal has to come from the same realm as
 * `fetch`, which is exactly the coupling that makes this function untestable outside a browser.
 */
async function get<T>(url: string): Promise<Result<T>> {
  const request: Promise<Result<T> | null> = fetch(url, {
    // Scan state changes while a worker is triaging it, so every render reads it again.
    cache: "no-store",
    headers: { accept: "application/json" },
  })
    .then(async (response): Promise<Result<T>> =>
      response.ok
        ? { ok: true, body: (await response.json()) as T }
        : { ok: false, status: response.status },
    )
    // A refused connection or a body that is not JSON are the same fact here: no answer.
    .catch(() => null);

  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const settled = await Promise.race([
      request,
      new Promise<null>((resolve) => {
        timer = setTimeout(() => resolve(null), TIMEOUT_MS);
      }),
    ]);
    return settled ?? { ok: false, status: 0 };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Reads the scan and its first page of findings on the server.
 *
 * This is what closes `/scan/[id]`'s cumulative layout shift. The list's shape — how many rows,
 * in which verdict groups — is only knowable once the scan's counts are in, and fetching them
 * from the browser means one paint with an empty list and a second with a full one. Reading them
 * here means the first paint *is* the list; the stream then updates rows that already exist.
 *
 * The findings page is best-effort on purpose: a scan whose summary reads but whose findings do
 * not is still worth rendering, with the counts and the empty list the client will fill.
 */
export async function loadScanSnapshot(scanId: string): Promise<ScanSnapshot> {
  const base = serverApiBase();
  if (base === null) return { state: "unavailable" };

  const path = `${base}/api/v1/scans/${encodeURIComponent(scanId)}`;
  const scan = await get<ScanSummary>(path);
  if (!scan.ok) return scan.status === 404 ? { state: "missing" } : { state: "error" };

  const page = await get<FindingPage>(`${path}/findings`);
  return { state: "ok", scan: scan.body, findings: page.ok ? page.body.items : [] };
}
