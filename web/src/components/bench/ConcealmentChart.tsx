import type { BreakdownRow } from "@/lib/bench";

import { BreakdownChart } from "./BreakdownChart";

/** One bar per concealment mechanism present in the data — never a fixed list of five. */
export function ConcealmentChart({ rows }: { rows: BreakdownRow[] }) {
  return (
    <BreakdownChart
      id="per-concealment"
      title="By concealment mechanism"
      lede="Concealment is crossed with tier rather than folded into it, so a hidden easy payload and
        a plain hard one stay distinguishable. If the ten-line unicode-hygiene detector beats the
        encoder on a bucket here, that is the honest result and it belongs in preprocessing."
      bucketLabel="Concealment"
      rows={rows}
    />
  );
}
