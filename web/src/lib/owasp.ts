/**
 * Mirror of `sieve/owasp.py` — OWASP MCP Top 10 (2025 beta) titles, verified against
 * https://owasp.org/www-project-mcp-top-10/ on 2026-09-12. The Python table is the source of
 * truth; this copy exists only so a chip can say what `MCP01` means without a round trip.
 * If the list revises, both tables move together.
 */
export const OWASP_TITLES: Readonly<Record<string, string>> = {
  MCP01: "Token mismanagement and secret exposure",
  MCP02: "Privilege escalation via scope creep",
  MCP03: "Tool poisoning",
  MCP05: "Command injection and execution",
  MCP06: "Intent flow subversion",
  MCP10: "Context injection and over-sharing",
};

export function owaspTitle(id: string): string {
  return OWASP_TITLES[id] ?? "OWASP MCP Top 10 entry";
}

/** Human labels for the seven sub-labels (PRD §9.2), in the product's sentence case. */
export const SUB_LABEL_TITLES: Readonly<Record<string, string>> = {
  exfiltration: "Exfiltration",
  instruction_override: "Instruction override",
  tool_shadowing: "Tool shadowing",
  privilege_escalation: "Privilege escalation",
  concealment: "Concealment",
  secret_solicitation: "Secret solicitation",
  command_execution: "Command execution",
};

export function subLabelTitle(id: string): string {
  return SUB_LABEL_TITLES[id] ?? id.replace(/_/g, " ");
}
