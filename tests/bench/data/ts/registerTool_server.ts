// Fixture: the `server.registerTool(name, config, handler)` form.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const server = new McpServer({ name: "weather", version: "2.0.0" });

server.registerTool(
  "get_current",
  {
    title: "Current weather",
    description: "Get current weather conditions for a location.",
    inputSchema: {
      location: z.string().describe("City name or 'lat,lon'"),
      units: z.string().optional(),
    },
  },
  async ({ location }) => ({ content: [{ type: "text", text: location }] }),
);

server.registerTool(
  "dynamic",
  {
    description: "Schema assembled at runtime.",
    inputSchema: buildShape(),
  },
  async () => ({ content: [] }),
);
