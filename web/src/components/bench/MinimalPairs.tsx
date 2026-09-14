import { formatPercent, type MinimalPairRow } from "@/lib/bench";

import { BenchSection, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/**
 * M11: a minimal pair is one tool written twice, once benign and once poisoned, differing by as
 * little as possible. Getting both halves right is the only score that cannot be reached by
 * learning which servers tend to be dangerous.
 */
export function MinimalPairs({ rows }: { rows: MinimalPairRow[] }) {
  return (
    <BenchSection
      id="minimal-pairs"
      title="Minimal pairs"
      lede="Both halves of a pair must be right for the pair to count. A model that has learned
        which servers look risky scores well on the corpus and badly here."
    >
      {rows.length === 0 ? (
        <EmptyNote>
          No minimal-pair results yet. They appear with the first{" "}
          <code className="t-mono">make bench</code>.
        </EmptyNote>
      ) : (
        <TableWrap label="Minimal pairs table">
          <thead>
            <tr>
              <Th>Tier</Th>
              <Th>Both halves correct</Th>
              <Th>Pairs</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.tier}>
                <Td>{row.tier}</Td>
                <Td>
                  <Num>{formatPercent(row.m11)}</Num>
                </Td>
                <Td>
                  <Num>{typeof row.n === "number" ? String(row.n) : "—"}</Num>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </BenchSection>
  );
}
