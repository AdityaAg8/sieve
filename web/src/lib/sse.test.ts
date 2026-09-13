import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ScanSummary } from "./api";
import { queryKeys } from "./queries";
import { useScanEvents } from "./sse";

/**
 * A stand-in for the browser's `EventSource`. jsdom ships none, and the behaviour under test is
 * precisely the part the real one hides: it reconnects by itself, replays from `Last-Event-ID`,
 * and reports the retry through `onerror` with `readyState === CONNECTING`. The fake lets a test
 * drive that sequence; what it cannot fake — that the header is sent — is the browser's job and
 * is covered end to end in `e2e/integration/`.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;

  readyState = 0;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  private readonly listeners = new Map<string, ((event: MessageEvent) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close(): void {
    this.readyState = this.CLOSED;
  }

  /** One server frame: `id:`, `event:` and `data:`. */
  emit(type: string, data: unknown, id = ""): void {
    this.readyState = this.OPEN;
    const event = { data: JSON.stringify(data), lastEventId: id } as MessageEvent;
    act(() => {
      for (const listener of this.listeners.get(type) ?? []) listener(event);
    });
  }

  /** The browser lost the connection. `retrying` is what `readyState` says while it retries. */
  fail({ retrying }: { retrying: boolean }): void {
    this.readyState = retrying ? this.CONNECTING : this.CLOSED;
    act(() => this.onerror?.(new Event("error")));
  }

  /** A reconnect got through. No frame has followed it yet. */
  reopen(): void {
    this.readyState = this.OPEN;
    act(() => this.onopen?.(new Event("open")));
  }
}

const SCAN = "00000000-0000-0000-0000-000000005eed";

function verdict(id: string) {
  return { id, scan_id: SCAN, result: { verdict: "dismissed" } };
}

function wrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

let client: QueryClient;

beforeEach(() => {
  FakeEventSource.instances = [];
  (globalThis as { EventSource?: unknown }).EventSource = FakeEventSource;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  delete (globalThis as { EventSource?: unknown }).EventSource;
  client.clear();
});

describe("useScanEvents against a stream that drops", () => {
  it("records the id of the last event, which is what a reconnect resumes from", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];

    source.emit("scan.started", { scan_id: SCAN }, "1");
    source.emit("tool.verdict", verdict("a"), "2");

    await waitFor(() => expect(result.current.lastEventId).toBe("2"));
  });

  it("stays on the stream while the browser is retrying, rather than failing over at once", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];

    source.emit("scan.started", { scan_id: SCAN }, "1");
    source.emit("tool.verdict", verdict("a"), "2");
    await waitFor(() => expect(result.current.status).toBe("streaming"));

    source.fail({ retrying: true });
    await waitFor(() => expect(result.current.findings).toHaveLength(1));
    expect(result.current.status).toBe("streaming");
  });

  it("counts a replayed verdict once, because Last-Event-ID replay overlaps by design", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];

    source.emit("tool.verdict", verdict("a"), "1");
    source.emit("tool.verdict", verdict("b"), "2");
    source.fail({ retrying: true });
    // What the server sends after a `Last-Event-ID: 2` reconnect that overlapped by one.
    source.emit("tool.verdict", verdict("b"), "2");
    source.emit("tool.verdict", verdict("c"), "3");

    await waitFor(() => expect(result.current.findings).toHaveLength(3));
    expect(result.current.findings.map((f) => f.id)).toEqual(["a", "b", "c"]);
  });

  it("gives up when the browser closes the stream for good", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    FakeEventSource.instances[0].fail({ retrying: false });
    await waitFor(() => expect(result.current.status).toBe("failed"));
  });

  it("gives up once the retries are exhausted", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];
    for (let attempt = 0; attempt < 6; attempt += 1) source.fail({ retrying: true });
    await waitFor(() => expect(result.current.status).toBe("failed"));
  });

  /**
   * A stream that reconnects but has nothing new to send — a completed scan, a quiet worker —
   * still spends the budget on every drop. Without `onopen` a long-lived page would eventually
   * fall back to the paged endpoint despite every reconnect having succeeded.
   */
  it("a reconnect that gets through refills the retry budget, frame or no frame", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];

    for (let round = 0; round < 4; round += 1) {
      for (let attempt = 0; attempt < 5; attempt += 1) source.fail({ retrying: true });
      source.reopen();
    }

    await waitFor(() => expect(result.current.status).not.toBe("failed"));
    source.emit("tool.verdict", verdict("a"), "1");
    await waitFor(() => expect(result.current.findings).toHaveLength(1));
  });
});

describe("useScanEvents and the scan summary cache", () => {
  /**
   * The worker's `scan.completed` payload is four counts and an id — not a `ScanSummary`. Writing
   * it straight into the cache would delete `expires_at`, `status` and `scanner_flagged` from a
   * summary the page has already rendered, which is how a live scan turns into an "expired" one.
   */
  it("merges the completion counts instead of replacing the cached summary", async () => {
    const cached = {
      id: SCAN,
      status: "running",
      source_type: "tools_json",
      tool_count: 27,
      scanner_flagged: 4,
      confirmed: 0,
      needs_review: 0,
      dismissed: 0,
      judge_calls: 0,
      judge_cost_usd: 0,
      judge_cost_source: null,
      model_version: "fixture@0",
      judge_model: null,
      created_at: "2026-09-13T00:00:00Z",
      expires_at: "2026-09-20T00:00:00Z",
      error: null,
    } as unknown as ScanSummary;
    client.setQueryData(queryKeys.scan(SCAN), cached);

    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    FakeEventSource.instances[0].emit(
      "scan.completed",
      { scan_id: SCAN, tool_count: 27, confirmed: 4, needs_review: 2, dismissed: 21 },
      "29",
    );

    await waitFor(() => expect(result.current.status).toBe("complete"));
    const after = client.getQueryData<ScanSummary>(queryKeys.scan(SCAN));
    expect(after?.scanner_flagged).toBe(4);
    expect(after?.expires_at).toBe("2026-09-20T00:00:00Z");
    expect(after?.confirmed).toBe(4);
    expect(after?.status).toBe("complete");
    // The merge goes both ways: the event's `scan_id` is not a field of `ScanSummary`, and a
    // cache row with keys the contract does not have is a contract nobody can rely on.
    expect(after).not.toHaveProperty("scan_id");
    expect(Object.keys(after ?? {}).sort()).toEqual(Object.keys(cached).sort());
  });

  it("reports the error the worker put on scan.failed", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    FakeEventSource.instances[0].emit(
      "scan.failed",
      { scan_id: SCAN, error: "RemoteFetchError: connection refused" },
      "3",
    );
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(result.current.error).toBe("RemoteFetchError: connection refused");
  });

  it("takes the tool count from scan.completed when scan.started did not carry one", async () => {
    const { result } = renderHook(() => useScanEvents(SCAN), { wrapper: wrapper(client) });
    const source = FakeEventSource.instances[0];
    source.emit("scan.started", { scan_id: SCAN }, "1");
    await waitFor(() => expect(result.current.status).toBe("streaming"));
    expect(result.current.expected).toBeNull();

    source.emit("scan.completed", { scan_id: SCAN, tool_count: 27 }, "2");
    await waitFor(() => expect(result.current.expected).toBe(27));
  });
});
