/**
 * Outbound destinations.
 *
 * None of these exist yet: the repository has no remote, the dataset and the model cards are not
 * published, and the write-up is not written. Rather than ship a plausible-looking URL that 404s,
 * every one is read from a `NEXT_PUBLIC_*` variable and is `null` until a deployment sets it.
 * A `null` destination renders as plain text with "not published yet" beside it — the same
 * discipline the numbers get (PRD §14.2: no invented metrics).
 */
export type Destination = { label: string; href: string | null };

const env = (value: string | undefined): string | null => (value && value.length > 0 ? value : null);

export const links = {
  repo: env(process.env.NEXT_PUBLIC_REPO_URL),
  writeup: env(process.env.NEXT_PUBLIC_WRITEUP_URL),
  dataset: env(process.env.NEXT_PUBLIC_DATASET_URL),
  models: env(process.env.NEXT_PUBLIC_MODEL_URL),
} as const;

export const footerDestinations: Destination[] = [
  { label: "Write-up", href: links.writeup },
  { label: "Dataset", href: links.dataset },
  { label: "Model card", href: links.models },
];

/**
 * The OWASP MCP Top 10 project page. The list is in beta and has no stable per-entry anchor,
 * so every chip links to the project page itself rather than to a guessed fragment; the entry
 * is identified by the chip's own accessible name.
 */
export const OWASP_MCP_TOP_10 = "https://owasp.org/www-project-mcp-top-10/";
