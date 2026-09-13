/**
 * Client-side validation for the scan input page.
 *
 * Two properties matter here beyond "does it parse".
 *
 * 1. Everything the user pastes or uploads is **untrusted data** (PRD SC-7). This module only
 *    reads it: it never evaluates it, never renders it as markup, and never sends the file
 *    anywhere but the scan endpoint the user asked for.
 * 2. The API's own limits (PRD §13.2: 1 MB body, 500 tools per scan) are checked here first, so
 *    a mistake is explained in plain language on the page instead of coming back as a 422.
 */
import type { ToolDefinition } from "./api";

/** PRD §13.2 body cap. */
export const MAX_BYTES = 1_000_000;
/** PRD §13.2 per-scan tool cap. */
export const MAX_TOOLS = 500;

export type ToolsResult =
  | { ok: true; toolCount: number; tools: ToolDefinition[]; payload: { tools: ToolDefinition[] } }
  | { ok: false; message: string };

export type ConfigResult =
  | { ok: true; serverCount: number; serverNames: string[]; payload: Record<string, unknown> }
  | { ok: false; message: string };

function fail(message: string): { ok: false; message: string } {
  return { ok: false, message };
}

/** 1-based line number of a byte offset. */
function lineOf(text: string, offset: number): number {
  let line = 1;
  for (let i = 0; i < offset && i < text.length; i += 1) {
    if (text[i] === "\n") line += 1;
  }
  return line;
}

/**
 * The line a `JSON.parse` failure points at. V8 spells the location two ways depending on the
 * version — "at position 312" and "(line 14 column 7)" — so both are read, and the offset wins
 * because it is exact.
 */
function lineFromParseError(text: string, error: unknown): number {
  const message = error instanceof Error ? error.message : String(error);
  const position = /position (\d+)/.exec(message);
  if (position) return lineOf(text, Number(position[1]));
  const line = /line (\d+)/.exec(message);
  if (line) return Number(line[1]);
  return 1;
}

function tooBig(text: string): boolean {
  return new Blob([text]).size > MAX_BYTES;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function validateToolsJson(text: string): ToolsResult {
  if (text.trim().length === 0) return fail("Paste a tools/list JSON to begin.");
  if (tooBig(text)) {
    return fail("This document is larger than 1 MB. Split it, or scan one server at a time.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return fail(
      `Could not parse this JSON. Expected a \`tools\` array. Check line ${lineFromParseError(text, error)}.`,
    );
  }

  const candidate = Array.isArray(parsed)
    ? parsed
    : isRecord(parsed) && Array.isArray(parsed.tools)
      ? parsed.tools
      : null;

  if (!candidate) {
    return fail(
      "This JSON parsed, but it has no `tools` array. Paste the result of a tools/list call.",
    );
  }

  if (candidate.length === 0) {
    return fail("This `tools` array is empty. There is nothing to triage.");
  }

  if (candidate.length > MAX_TOOLS) {
    return fail(
      `A scan takes at most ${MAX_TOOLS} tools; this one has ${candidate.length}. Split it by server.`,
    );
  }

  const named = candidate.every(
    (tool) => isRecord(tool) && typeof tool.name === "string" && tool.name.length > 0,
  );
  if (!named) {
    return fail("This looks close, but every tool needs a `name`. Check the entries and retry.");
  }

  const tools = candidate as unknown as ToolDefinition[];
  return { ok: true, toolCount: tools.length, tools, payload: { tools } };
}

export function validateMcpConfig(text: string): ConfigResult {
  if (text.trim().length === 0) return fail("Choose an mcp.json to begin.");
  if (tooBig(text)) {
    return fail("This file is larger than 1 MB. Split it, or scan one server at a time.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (error) {
    return fail(
      `Could not parse this JSON. Expected an \`mcpServers\` object. Check line ${lineFromParseError(text, error)}.`,
    );
  }

  if (!isRecord(parsed) || !isRecord(parsed.mcpServers)) {
    return fail(
      "This JSON parsed, but it has no `mcpServers` object. Upload the mcp.json your client reads.",
    );
  }

  const serverNames = Object.keys(parsed.mcpServers);
  if (serverNames.length === 0) {
    return fail("This `mcpServers` object is empty. There is nothing to triage.");
  }

  return { ok: true, serverCount: serverNames.length, serverNames, payload: parsed };
}

/**
 * A remote MCP server URL. Mirrors the worker's own rule (PRD §13.6) so the page can refuse the
 * obvious cases without a round trip; the sandboxed worker re-checks everything and is the
 * authority. This is a convenience, never a security boundary.
 */
export function validateRemoteUrl(text: string): { ok: true; url: string } | { ok: false; message: string } {
  const trimmed = text.trim();
  if (trimmed.length === 0) return fail("Enter the https:// URL of a streamable HTTP MCP server.");

  let url: URL;
  try {
    url = new URL(trimmed);
  } catch {
    return fail("That is not a URL. It should start with https:// and name a host.");
  }

  if (url.protocol !== "https:") return fail("Remote scans accept https:// only.");
  if (url.username || url.password) {
    return fail("Remove the credentials from this URL. Sieve will not send them.");
  }
  if (url.port && url.port !== "443") return fail("Remote scans accept port 443 only.");

  return { ok: true, url: url.toString() };
}
