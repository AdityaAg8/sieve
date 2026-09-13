/**
 * MSW handlers. Everything the web app talks to is mocked from the repository fixtures, so
 * lane L6 can be built and tested with no API server, no database and no Docker.
 *
 * The data is `fixtures/verdicts/sample-27.json`, copied into `src/mocks/data/` by
 * `scripts/copy-fixtures.mjs`. Note what it actually says: the golden scanner flagged **4** of
 * 27 tools, so `summary.scanner_flagged` is 4. Nothing in the app may assume 27 or 6.
 */
import { HttpResponse, delay, http } from "msw";

import verdicts from "./data/verdicts-sample-27.json";
import type {
  BenchSummary,
  Finding,
  FindingPage,
  ModelInfo,
  ScanSummary,
  TriageResult,
} from "@/lib/api";

const FIXTURE = verdicts as unknown as {
  meta: { provisional: boolean };
  summary: ScanSummary;
  findings: Finding[];
};

/** Milliseconds between mocked SSE events. 27 tools + started + completed = 29 events. */
export const SSE_SPACING_MS = 30;

export const sampleSummary = FIXTURE.summary;
export const sampleFindings = FIXTURE.findings;

/** The scan summary as it looks before any tool has been triaged. */
function queuedSummary(id: string): ScanSummary {
  return {
    ...sampleSummary,
    id,
    status: "queued",
    scanner_flagged: 0,
    confirmed: 0,
    needs_review: 0,
    dismissed: 0,
    judge_calls: 0,
    judge_cost_usd: 0,
  };
}

function completedSummary(id: string): ScanSummary {
  return { ...sampleSummary, id, status: "complete" };
}

/** A provisional summary: `make bench` has not run, so the app must render `—` everywhere. */
export const provisionalBench: BenchSummary = {
  dataset_version: "sievebench@unreleased",
  model_version: sampleSummary.model_version ?? "fixture@0",
  provisional: true,
  generated_at: sampleSummary.created_at,
  headline: {},
  tables: {},
};

export const mockModels: ModelInfo = {
  model_version: sampleSummary.model_version ?? "fixture@0",
  t_low: 0.2,
  t_high: 0.8,
  temperature: 1.0,
  onnx: true,
  judge_model: sampleSummary.judge_model,
  // PRD §13.2: the public demo runs judge-off by default, so this is the normal state.
  judge_enabled: false,
};

function sarifFor(scanId: string) {
  const level = { confirmed: "error", needs_review: "warning", dismissed: "note" } as const;
  return {
    $schema: "https://json.schemastore.org/sarif-2.1.0.json",
    version: "2.1.0",
    runs: [
      {
        tool: {
          driver: {
            name: "Sieve",
            version: sampleSummary.model_version ?? "fixture@0",
            informationUri: "https://github.com/aditya-tr-cyber/sieve",
            rules: [],
          },
        },
        results: sampleFindings.map((finding) => ({
          ruleId: finding.result.sub_labels[0] ?? finding.result.predicted_class,
          level: level[finding.result.verdict],
          message: { text: finding.result.reasoning ?? finding.result.predicted_class },
          locations: [
            {
              physicalLocation: {
                artifactLocation: { uri: `${scanId}/${finding.id}` },
              },
            },
          ],
          properties: {
            poisoned_probability: finding.result.poisoned_probability,
            predicted_class: finding.result.predicted_class,
            stage: finding.result.stage,
            model_version: finding.result.model_version,
          },
        })),
      },
    ],
  };
}

const API = "*/api/v1";

/**
 * A test seam that lives entirely in the mock layer. A Playwright test can put a partial
 * response under `sieve.mock.<name>` in `localStorage` before navigating, and the handler merges
 * it over the default — which is how the suite exercises "judge on" and "judge off", or a
 * non-provisional benchmark, without a second set of handlers. No application code reads this.
 */
function override(name: "models" | "bench"): Record<string, unknown> {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(`sieve.mock.${name}`) ?? "{}") as Record<
      string,
      unknown
    >;
  } catch {
    return {};
  }
}

export const handlers = [
  http.get(`${API}/healthz`, () => HttpResponse.json({ status: "ok" })),
  http.get(`${API}/readyz`, () => HttpResponse.json({ status: "ok" })),

  http.get(`${API}/models`, () => HttpResponse.json({ ...mockModels, ...override("models") })),

  http.get(`${API}/bench/summary`, () =>
    HttpResponse.json({ ...provisionalBench, ...override("bench") }),
  ),

  http.post(`${API}/scans`, async () =>
    HttpResponse.json(queuedSummary(sampleSummary.id), { status: 202 }),
  ),

  http.get(`${API}/scans/:scanId`, ({ params }) =>
    HttpResponse.json(completedSummary(String(params.scanId))),
  ),

  http.get(`${API}/scans/:scanId/findings`, ({ request }) => {
    const query = new URL(request.url).searchParams;
    const verdict = query.get("verdict");
    const predictedClass = query.get("predicted_class");
    const subLabel = query.get("sub_label");
    const stage = query.get("stage");

    const items = sampleFindings.filter(
      (finding) =>
        (!verdict || finding.result.verdict === verdict) &&
        (!predictedClass || finding.result.predicted_class === predictedClass) &&
        (!subLabel || finding.result.sub_labels.includes(subLabel as never)) &&
        (!stage || finding.result.stage === stage),
    );

    const page: FindingPage = { items, total: items.length, next_cursor: null };
    return HttpResponse.json(page);
  }),

  http.get(`${API}/scans/:scanId/sarif`, ({ params }) =>
    HttpResponse.json(sarifFor(String(params.scanId)), {
      headers: { "content-disposition": `attachment; filename="${params.scanId}.sarif"` },
    }),
  ),

  http.post(`${API}/triage`, () =>
    HttpResponse.json(sampleFindings[0].result satisfies TriageResult),
  ),

  http.post(`${API}/findings/:findingId/feedback`, () => new HttpResponse(null, { status: 204 })),

  /**
   * The live stream. 29 events at 30 ms spacing: `scan.started`, one `tool.verdict` per tool,
   * then `scan.completed`. Verdict events arrive in fixture order, which is not verdict order —
   * the results page has to group them itself, exactly as it will against the real worker.
   */
  http.get(`${API}/scans/:scanId/events`, ({ params }) => {
    const scanId = String(params.scanId);
    const encoder = new TextEncoder();
    const frame = (id: number, event: string, data: unknown) =>
      encoder.encode(`id: ${id}\nevent: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

    const stream = new ReadableStream({
      async start(controller) {
        controller.enqueue(
          frame(0, "scan.started", { scan_id: scanId, tool_count: sampleFindings.length }),
        );
        for (const [index, finding] of sampleFindings.entries()) {
          await delay(SSE_SPACING_MS);
          controller.enqueue(frame(index + 1, "tool.verdict", { ...finding, scan_id: scanId }));
        }
        await delay(SSE_SPACING_MS);
        controller.enqueue(
          frame(sampleFindings.length + 1, "scan.completed", completedSummary(scanId)),
        );
        controller.close();
      },
    });

    return new HttpResponse(stream, {
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache",
        connection: "keep-alive",
      },
    });
  }),
];
