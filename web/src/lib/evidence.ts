/**
 * Turning evidence spans into something a reader can see.
 *
 * A span is `{field, path, quote, start, end}` where the offsets may be null — the judge is
 * asked to quote verbatim, and when its quote cannot be located in the text the span is kept
 * rather than dropped, because "the judge said something that is not in the tool" is itself a
 * finding a reviewer needs to see.
 */
import type { EvidenceSpan } from "./api";

export type MergedSpan = { start: number; end: number; quotes: string[] };

/**
 * Located spans, sorted and merged. Two spans that overlap — or merely touch — become one
 * `<mark>`, because nesting or abutting marks renders as a visual seam that looks like a bug.
 */
export function mergeSpans(spans: EvidenceSpan[], textLength: number): MergedSpan[] {
  const located = spans
    .filter(
      (span) =>
        typeof span.start === "number" &&
        typeof span.end === "number" &&
        span.start >= 0 &&
        span.end > span.start &&
        span.start < textLength,
    )
    .map((span) => ({
      start: span.start as number,
      end: Math.min(span.end as number, textLength),
      quotes: [span.quote],
    }))
    .sort((a, b) => a.start - b.start || a.end - b.end);

  return located.reduce<MergedSpan[]>((acc, span) => {
    const previous = acc.at(-1);
    if (previous && span.start <= previous.end) {
      const merged: MergedSpan = {
        start: previous.start,
        end: Math.max(previous.end, span.end),
        quotes: [...previous.quotes, ...span.quotes],
      };
      return [...acc.slice(0, -1), merged];
    }
    return [...acc, span];
  }, []);
}

/** Spans the judge quoted but could not locate in the text. */
export function unlocatedQuotes(spans: EvidenceSpan[]): string[] {
  return spans
    .filter((span) => typeof span.start !== "number" || typeof span.end !== "number")
    .map((span) => span.quote)
    .filter((quote) => quote.length > 0);
}

/** Reads a JSON pointer, returning the value only when it is a string. */
export function resolvePointer(root: unknown, pointer: string | null): string | null {
  if (!pointer) return typeof root === "string" ? root : null;
  let cursor: unknown = root;
  for (const raw of pointer.split("/").slice(1)) {
    const key = raw.replace(/~1/g, "/").replace(/~0/g, "~");
    if (typeof cursor !== "object" || cursor === null) return null;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return typeof cursor === "string" ? cursor : null;
}

export type EvidenceBlock = {
  /** Stable key and visible heading, e.g. `inputSchema/properties/content/description`. */
  label: string;
  field: EvidenceSpan["field"];
  path: string | null;
  text: string;
  spans: EvidenceSpan[];
};

type ToolLike = {
  name: string;
  title?: string | null;
  description?: string | null;
  inputSchema: Record<string, unknown>;
  outputSchema?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
};

/**
 * The blocks the drawer shows, in reading order: the description first, then every other place
 * a span points at. A field with no text to show is skipped rather than rendered empty.
 */
export function evidenceBlocks(tool: ToolLike, spans: EvidenceSpan[]): EvidenceBlock[] {
  const sources: Record<EvidenceSpan["field"], unknown> = {
    name: tool.name,
    title: tool.title ?? null,
    description: tool.description ?? null,
    inputSchema: tool.inputSchema,
    outputSchema: tool.outputSchema ?? null,
    annotations: tool.annotations ?? null,
  };

  const keys = new Map<string, { field: EvidenceSpan["field"]; path: string | null }>();
  if (tool.description) keys.set("description", { field: "description", path: null });
  for (const span of spans) {
    keys.set(`${span.field}${span.path ?? ""}`, { field: span.field, path: span.path });
  }

  return [...keys.entries()].flatMap(([label, { field, path }]) => {
    const text = resolvePointer(sources[field], path);
    if (!text) return [];
    const blockSpans = spans.filter(
      (span) => span.field === field && (span.path ?? null) === (path ?? null),
    );
    return [{ label, field, path, text, spans: blockSpans }];
  });
}
