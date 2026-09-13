import type { BreakdownRow } from "@/lib/bench";

import { BreakdownChart } from "./BreakdownChart";

/** Tiers are semantic (PRD §9.5), not a function of how much text was obfuscated. */
export function TierChart({ rows }: { rows: BreakdownRow[] }) {
  return (
    <BreakdownChart
      id="per-tier"
      title="By difficulty tier"
      lede="Tiers are semantic: an easy row states its intent in one sentence, a hard row requires
        following a multi-step consequence. They are not a measure of how much the text was
        obfuscated — that is the concealment attribute below, and it is orthogonal."
      bucketLabel="Tier"
      rows={rows}
    />
  );
}
