import { formatPercent, type HumanReference as HumanPoint } from "@/lib/bench";

import { BenchSection, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/**
 * M12. "Is 0.94 recall good?" has no answer without a scale. The second annotator's own
 * (recall, FPR) point is that scale: it is what a careful person achieved on the same rows.
 *
 * Two numbers for one or two entities is a table, not a chart — plotting two points on an ROC
 * axis would be chart junk dressed as rigour.
 */
export function HumanReference({ points }: { points: HumanPoint[] }) {
  return (
    <BenchSection
      id="human-reference"
      title="Human reference point"
      lede="The second annotator read the same rows blind. Their recall and false-positive rate are
        the scale against which the model’s numbers mean anything at all."
    >
      {points.length === 0 ? (
        <EmptyNote>
          No human reference yet. It appears with the first{" "}
          <code className="t-mono">make bench</code>.
        </EmptyNote>
      ) : (
        <TableWrap>
          <thead>
            <tr>
              <Th>Reader</Th>
              <Th>Recall</Th>
              <Th>False-positive rate</Th>
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.label}>
                <Td>{point.label}</Td>
                <Td>
                  <Num>{formatPercent(point.recall)}</Num>
                </Td>
                <Td>
                  <Num>{formatPercent(point.fpr)}</Num>
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </BenchSection>
  );
}
