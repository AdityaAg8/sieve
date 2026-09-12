// Fixture: the positional `server.tool(name, description, shape, handler)` form.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

const server = new McpServer({ name: "files", version: "1.0.0" });

server.tool(
  "read_file",
  "Reads a file.",
  {
    path: z.string().describe("Absolute path"),
    limit: z.number().optional().describe("Maximum bytes"),
    verbose: z.boolean().optional(),
    filters: z.array(z.string()),
  },
  async ({ path }) => ({ content: [{ type: "text", text: path }] }),
);

server.tool("ping", { note: z.string() }, async () => ({ content: [] }));

notServer.notTool("decoy", "Not a tool at all.", {}, async () => ({}));
