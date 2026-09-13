/**
 * Typed fetchers for the Sieve API.
 *
 * Every request and response type is projected out of `paths` in `api-types.ts`, which is
 * generated from `schemas/openapi.json`. Nothing in this file hand-writes a shape: if the
 * contract moves, `npm run gen:types` moves these types and `tsc` finds every caller.
 */
import { apiBase } from "./config";
import type { components, paths } from "./api-types";

type Json<T> = T extends { content: { "application/json": infer B } } ? B : never;
type Ok<P, M extends keyof P, C extends number> = P[M] extends {
  responses: infer R;
}
  ? C extends keyof R
    ? Json<R[C]>
    : never
  : never;

export type ScanSummary = components["schemas"]["ScanSummary"];
export type Finding = components["schemas"]["Finding"];
export type FindingPage = components["schemas"]["FindingPage"];
export type ModelInfo = components["schemas"]["ModelInfo"];
export type BenchSummary = components["schemas"]["BenchSummary"];
export type TriageResult = components["schemas"]["TriageResult"];
export type ScanCreate = components["schemas"]["ScanCreate"];
export type FeedbackCreate = components["schemas"]["FeedbackCreate"];
export type ScannerFinding = components["schemas"]["ScannerFinding"];
export type EvidenceSpan = components["schemas"]["EvidenceSpan"];
export type ToolDefinition = components["schemas"]["ToolDefinition"];

export type Verdict = TriageResult["verdict"];
export type PredictedClass = TriageResult["predicted_class"];
export type Stage = TriageResult["stage"];
export type SubLabel = TriageResult["sub_labels"][number];

export type FindingQuery = NonNullable<
  paths["/api/v1/scans/{scan_id}/findings"]["get"]["parameters"]["query"]
>;

/** Thrown for any non-2xx response so callers see the status and the server's own words. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly statusText: string,
    readonly body: string,
  ) {
    super(`${status} ${statusText}`);
    this.name = "ApiError";
  }
}

function url(path: string, query?: Record<string, unknown>): string {
  const base = apiBase();
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const qs = search.toString();
  return `${base}${path}${qs ? `?${qs}` : ""}`;
}

async function request<T>(input: string, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    headers: { accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText, await response.text());
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function createScan(
  body: ScanCreate,
): Promise<Ok<paths["/api/v1/scans"], "post", 202>> {
  return request(url("/api/v1/scans"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getScan(
  scanId: string,
): Promise<Ok<paths["/api/v1/scans/{scan_id}"], "get", 200>> {
  return request(url(`/api/v1/scans/${encodeURIComponent(scanId)}`));
}

export function getFindings(
  scanId: string,
  query?: FindingQuery,
): Promise<Ok<paths["/api/v1/scans/{scan_id}/findings"], "get", 200>> {
  return request(url(`/api/v1/scans/${encodeURIComponent(scanId)}/findings`, query));
}

export function triage(
  body: paths["/api/v1/triage"]["post"] extends { requestBody: { content: { "application/json": infer B } } }
    ? B
    : never,
): Promise<Ok<paths["/api/v1/triage"], "post", 200>> {
  return request(url("/api/v1/triage"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function getBench(): Promise<Ok<paths["/api/v1/bench/summary"], "get", 200>> {
  return request(url("/api/v1/bench/summary"));
}

export function getModels(): Promise<Ok<paths["/api/v1/models"], "get", 200>> {
  return request(url("/api/v1/models"));
}

export async function sendFeedback(findingId: string, body: FeedbackCreate): Promise<void> {
  await request<void>(url(`/api/v1/findings/${encodeURIComponent(findingId)}/feedback`), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** The SARIF document's address. Used by the `e` shortcut and the export control. */
export function sarifUrl(scanId: string): string {
  return url(`/api/v1/scans/${encodeURIComponent(scanId)}/sarif`);
}

/** The SSE stream's address. `sse.ts` owns the connection; this keeps the path in one place. */
export function eventsUrl(scanId: string): string {
  return url(`/api/v1/scans/${encodeURIComponent(scanId)}/events`);
}
