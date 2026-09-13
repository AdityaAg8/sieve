import type { ConfusionMatrix as Matrix } from "@/lib/bench";

import { BenchSection, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/**
 * The cell that matters is `imperative_benign → flagged`: a legitimate "you MUST call this first"
 * misread as an attack is the exact failure this project exists to remove, so it is called out
 * rather than left for the reader to find.
 */
export function ConfusionMatrix({ matrix }: { matrix: Matrix | undefined }) {
  const calledOut = matrix?.called_out;

  return (
    <BenchSection
      id="confusion"
      title="Confusion matrix"
      lede="Rows are the true class, columns are Sieve’s. The highlighted cell is
        imperative-benign read as poisoned — the false positive the whole pipeline exists to
        remove."
    >
      {!matrix ? (
        <EmptyNote>
          No matrix yet. It appears with the first <code className="t-mono">make bench</code>.
        </EmptyNote>
      ) : (
        <TableWrap>
          <thead>
            <tr>
              <Th>True \ predicted</Th>
              {matrix.labels.map((label) => (
                <Th key={label}>{label}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.counts.map((row, rowIndex) => (
              <tr key={matrix.labels[rowIndex] ?? rowIndex}>
                <Th scope="row">{matrix.labels[rowIndex]}</Th>
                {row.map((count, columnIndex) => {
                  const highlighted =
                    calledOut?.[0] === rowIndex && calledOut?.[1] === columnIndex;
                  return (
                    <Td key={columnIndex}>
                      <span
                        data-red={highlighted ? "destructive" : undefined}
                        className={highlighted ? "text-red-0 border-red-0 border-b" : undefined}
                        title={
                          highlighted
                            ? "Imperative-benign read as poisoned — the false positive Sieve exists to remove"
                            : undefined
                        }
                      >
                        <Num>{String(count)}</Num>
                      </span>
                      {highlighted ? (
                        <span className="sr-only">
                          {" "}
                          — imperative-benign read as poisoned, the false positive Sieve exists to
                          remove
                        </span>
                      ) : null}
                    </Td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </BenchSection>
  );
}
