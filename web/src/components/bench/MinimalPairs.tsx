import { formatPercent, type MinimalPairRow } from "@/lib/bench";

import { BenchSection, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/**
 * M11: a minimal pair is one tool written twice, once benign and once poisoned, differing by as
 * little as possible. Getting both halves right is the only score that cannot be reached by
 * learning which servers tend to be dangerous. Read off the ablation rows that score pairs
 * (`minimal_pair_accuracy`, training plan H5); a judge row's figure is deferred with the judge.
 */
export function MinimalPairs({ rows, deferred }: { rows: MinimalPairRow[]; deferred: number[] }) {
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
              <Th>#</Th>
              <Th>Configuration</Th>
              <Th>Both halves correct</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.row}>
                <Td>
                  <Num>{String(row.row)}</Num>
                </Td>
                <Td>{row.name}</Td>
                <Td>
                  <Num>{formatPercent(row.m11)}</Num>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
      {deferred.length > 0 ? (
        <p className="t-ui text-ink-3" data-testid="minimal-pairs-deferred">
          Deferred: {deferred.length === 1 ? "row" : "rows"} {deferred.join(", ")}{" "}
          {deferred.length === 1 ? "needs" : "need"} the LLM judge, which moves to v1.1 (D8).
        </p>
      ) : null}
    </BenchSection>
  );
}
