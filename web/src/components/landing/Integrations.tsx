/**
 * Names, not logos: reproducing a third party's mark would be a brand-usage decision this
 * project has not earned. Each row says what the integration actually does, in the same
 * vocabulary the rest of the app uses.
 */
const INTEGRATIONS = [
  { name: "cisco-ai-mcp-scanner", role: "Read its YARA findings and triage them" },
  { name: "snyk agent-scan", role: "Read its report; never runs a server for it" },
  { name: "SARIF 2.1.0", role: "Export findings into GitHub code scanning" },
  { name: "GitHub Action", role: "Fail a build on confirmed findings only" },
  { name: "MCP server", role: "Let your own agent check a server before trusting it" },
  { name: "CLI", role: "Run offline in an air-gapped pipeline, no API key" },
] as const;

export function Integrations() {
  return (
    <section
      aria-labelledby="integrations-heading"
      className="border-line-1 mx-auto max-w-[84rem] border-t px-4 py-16 sm:px-8"
    >
      <h2 id="integrations-heading" className="t-h2 text-ink-0">
        Fits into
      </h2>

      <ul className="mt-10 grid gap-x-8 gap-y-6 sm:grid-cols-2 lg:grid-cols-3">
        {INTEGRATIONS.map((integration) => (
          <li key={integration.name} className="border-line-1 flex flex-col gap-1 border-t pt-4">
            <p className="t-mono text-ink-0 break-words">{integration.name}</p>
            <p className="t-ui text-ink-2">{integration.role}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}
