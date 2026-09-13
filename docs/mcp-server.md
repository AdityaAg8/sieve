# Sieve as an MCP server

Sieve exposes its triage layer over MCP, so an agent can ask "is this tool definition
poisoned?" the same way it asks anything else. It is a triage service and nothing more: the
tools take tool definitions as **data** and return verdicts. None of them starts, connects to,
installs or otherwise executes another MCP server (PRD SC-1), and everything inside a submitted
definition is treated as untrusted text (SC-7), redacted before the judge sees it (SC-5),
because every tool goes through `sieve.pipeline.triage` like every other Sieve surface.

## The three tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `triage_tool_definition` | `tool`, `sibling_tool_names` (optional) | one `TriageResult` |
| `scan_tools_list` | `tools` | `summary` counts plus one `TriageResult` per tool |
| `explain_verdict` | `scan_id`, `finding_id` | `found`, `verdict`, `stage`, `sub_labels`, `evidence_spans`, `reasoning`, `remediation`, `owasp` |

Input and output schemas are generated from the Pydantic models in `sieve/types.py`, so a
verdict on the wire is the same object the HTTP API and the CLI return.

`explain_verdict` reads a verdict Sieve already stored, so it needs a Sieve API and therefore
`--api-url`; started without one it answers `isError: true` and says so, rather than inventing
an explanation. It takes both ids because the API's findings route is scan-scoped and paged:
the tool reads `GET /api/v1/scans/{scan_id}/findings` and follows `next_cursor` until the id
turns up or the pages run out. An id the API does not carry is an **answer**, not an error —
`found: false` with a `message` saying whether the scan or the finding was the missing one (a
404 also names `--api-url`, because "wrong scan id" and "right id, wrong Sieve" look identical
from here). A transport failure, a redirect or a 5xx is a genuine error and comes back as one.

## Running it

```
python -m sieve.mcp_server --transport stdio
python -m sieve.mcp_server --transport http --port 8765
python -m sieve.mcp_server --transport stdio --api-url http://localhost:8000
```

`--transport http` is the spec's streamable HTTP transport. stdio is the default, because that
is how a client that starts the process itself talks to it.

Which classifier and judge answer is `SIEVE_MODEL_ID` and `ANTHROPIC_API_KEY`, exactly as for
the API and the worker: with neither set, the shared `FakeClassifier` and `FakeJudge` serve,
and both say so at INFO on stderr (stdout is the protocol channel and carries nothing else).

## Adding it to Claude Code

```
claude mcp add sieve -- uv run python -m sieve.mcp_server --transport stdio
```

Or, as a project-scoped `.mcp.json` checked in beside the repo:

```json
{
  "mcpServers": {
    "sieve": {
      "command": "uv",
      "args": ["run", "python", "-m", "sieve.mcp_server", "--transport", "stdio"]
    }
  }
}
```

Add `"--api-url", "http://localhost:8000"` to `args` to turn `explain_verdict` on.

## Verification

### Verified: the entry point answers `tools/list` over stdio

Raw JSON-RPC, no SDK client in the way — three lines in, the handshake and the tool list out:

```bash
uv run python -c "
import sys, time
for m in [
 '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-11-25\",\"capabilities\":{},\"clientInfo\":{\"name\":\"by-hand\",\"version\":\"0\"}}}',
 '{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}',
 '{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/list\",\"params\":{}}',
]:
    sys.stdout.write(m + '\n'); sys.stdout.flush()
time.sleep(4)
" | uv run python -m sieve.mcp_server --transport stdio
```

The `initialize` reply, verbatim:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "capabilities": {
      "experimental": {},
      "prompts": { "listChanged": false },
      "resources": { "listChanged": false, "subscribe": false },
      "tools": { "listChanged": false }
    },
    "instructions": "Sieve triages MCP tool definitions. It classifies a single tool definition, or every tool in a tools/list response, and returns a verdict for each one with the evidence behind it. It reads definitions as text; it does not start, connect to, or install any MCP server.",
    "protocolVersion": "2025-11-25",
    "serverInfo": { "name": "sieve", "version": "0.1.0" }
  }
}
```

And the three tools from the `tools/list` reply, schemas elided:

```
tool: triage_tool_definition | type = object | required = ['tool']
      Classifies one MCP tool definition as confirmed, needs review, or dismissed, with evidence.
tool: scan_tools_list | type = object | required = ['tools']
      Classifies every tool in a tools/list response and returns per-tool verdicts and counts.
tool: explain_verdict | type = object | required = ['scan_id', 'finding_id']
      Returns the evidence and reasoning stored for a previous finding id. It needs the id of the scan that finding belongs to, and a Sieve API named by this server's --api-url.
```

Both halves of that are pinned as tests:
`tests/mcp/test_transports.py::test_stdio_advertises_a_static_tool_list_on_the_classic_handshake`
repeats this exchange against a real subprocess, and
`test_python_m_sieve_mcp_server_answers_tools_list_over_stdio` does the same through the SDK's
own client.

### A caveat on `tools.listChanged`

`false` above is the 2025-11-25 handshake. A client that negotiates 2026-07-28 is told `true`
instead: at that revision the SDK derives the flag from the `subscriptions/listen` endpoint it
serves unconditionally, so the value is the SDK's rather than Sieve's, and there is no
supported way to turn it off. Either way Sieve's tool list is static and Sieve never sends a
tools-changed notification.

### Not verified: the three tools inside a Claude Code session

Running `claude mcp add` and reading the tool list back inside an interactive Claude Code
session needs a human at a terminal, so it has **not** been done, and no transcript of it is
reproduced here. To close it:

1. `claude mcp add sieve -- uv run python -m sieve.mcp_server --transport stdio`
2. Start `claude` in this repository and run `/mcp`; `sieve` should be listed as connected.
3. Ask it to triage a tool definition — `fixtures/tools/sample-27.json` has 27, of which
   `write_file` carries an `~/.ssh/id_rsa` instruction in a parameter description and should
   come back `confirmed`.
4. Paste the redacted excerpt into this section and delete this list.
