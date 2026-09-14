import { formatInterval, type AblationRow } from "@/lib/bench";

import { BenchSection, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/** The fourteen rows of PRD §11.5, all on pooled out-of-fold predictions. */
export function AblationTable({ rows }: { rows: AblationRow[] }) {
  return (
    <BenchSection
      id="ablations"
      title="Ablations"
      lede="All fourteen rows are computed on pooled out-of-fold predictions from the 5-fold grouped
        cross-validation over development servers. None of them has seen the holdout."
    >
      {rows.length === 0 ? (
        <EmptyNote>
          No ablations yet. They appear with the first <code className="t-mono">make bench</code>.
        </EmptyNote>
      ) : (
        <TableWrap label="Ablations table">
          <thead>
            <tr>
              <Th>#</Th>
              <Th>Configuration</Th>
              <Th>Recall [CI]</Th>
              <Th>FPR [CI]</Th>
              <Th>Macro F1 [CI]</Th>
              <Th>Note</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)}>
                <Td>
                  <Num>{String(row.id)}</Num>
                </Td>
                <Td>{row.name}</Td>
                <Td>
                  <Num>{formatInterval(row.recall)}</Num>
                </Td>
                <Td>
                  <Num>{formatInterval(row.fpr)}</Num>
                </Td>
                <Td>
                  <Num>{formatInterval(row.macro_f1)}</Num>
                </Td>
                <Td>
                  <span className="text-ink-2">{row.note ?? ""}</span>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </BenchSection>
  );
}
