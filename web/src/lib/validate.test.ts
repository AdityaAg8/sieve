import { describe, expect, it } from "vitest";

import { MAX_BYTES, MAX_TOOLS, validateMcpConfig, validateToolsJson } from "./validate";

/** A document that is valid JSON until line 14, where the comma is missing. */
const BROKEN = `{
  "tools": [
    {
      "name": "get_current",
      "inputSchema": { "type": "object" }
    },
    {
      "name": "get_forecast",
      "inputSchema": { "type": "object" }
    },
    {
      "name": "search_city",
      "inputSchema": { "type": "object" }
      "description": "Missing a comma on the line above."
    }
  ]
}`;

const VALID = JSON.stringify({
  tools: [
    { name: "a", inputSchema: { type: "object" } },
    { name: "b", inputSchema: { type: "object" } },
  ],
});

describe("validateToolsJson", () => {
  it("names the line the document broke on", () => {
    const result = validateToolsJson(BROKEN);
    expect(result).toEqual({
      ok: false,
      message: "Could not parse this JSON. Expected a `tools` array. Check line 14.",
    });
  });

  it("returns the tool count for a valid tools/list dump", () => {
    const result = validateToolsJson(VALID);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.toolCount).toBe(2);
      expect(result.tools[0].name).toBe("a");
    }
  });

  it("accepts a bare array of tools", () => {
    const result = validateToolsJson(JSON.stringify([{ name: "a", inputSchema: {} }]));
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.toolCount).toBe(1);
  });

  it("asks for a tools array when the document parses but has no tools", () => {
    const result = validateToolsJson(JSON.stringify({ servers: {} }));
    expect(result).toEqual({
      ok: false,
      message:
        "This JSON parsed, but it has no `tools` array. Paste the result of a tools/list call.",
    });
  });

  it("says what to do when the box is empty", () => {
    expect(validateToolsJson("   ")).toEqual({
      ok: false,
      message: "Paste a tools/list JSON to begin.",
    });
  });

  it("refuses a tool entry without a name", () => {
    const result = validateToolsJson(JSON.stringify({ tools: [{ inputSchema: {} }] }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain("every tool needs a `name`");
  });

  it("refuses more tools than a scan accepts", () => {
    const tools = Array.from({ length: MAX_TOOLS + 1 }, (_, i) => ({
      name: `t${i}`,
      inputSchema: {},
    }));
    const result = validateToolsJson(JSON.stringify({ tools }));
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain(`${MAX_TOOLS} tools`);
  });

  it("refuses a document over the body cap before sending it anywhere", () => {
    const result = validateToolsJson(`{"tools":[${" ".repeat(MAX_BYTES)}]}`);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain("1 MB");
  });
});

describe("validateMcpConfig", () => {
  it("counts the servers in an mcp.json", () => {
    const result = validateMcpConfig(
      JSON.stringify({ mcpServers: { weather: { command: "uvx" }, notes: { command: "npx" } } }),
    );
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.serverCount).toBe(2);
  });

  it("says what an mcp.json is missing", () => {
    const result = validateMcpConfig(JSON.stringify({ tools: [] }));
    expect(result).toEqual({
      ok: false,
      message:
        "This JSON parsed, but it has no `mcpServers` object. Upload the mcp.json your client reads.",
    });
  });
});
