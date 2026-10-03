import { NO_NUMBER, formatPercent, formatPercentRange, type LabelAudit } from "@/lib/bench";

import { auditLabel } from "./Agreement";
import { BenchSection, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/**
 * The scale under every number on this page is label quality, and v1 measures it one way:
 * human gold vs the labelling ensemble on the 104-row sealed audit (`label_audit.json`, training
 * plan F3). test-ID holds no poisoned row, so this is an error rate on labels — never a human's
 * recall, and never a second annotator's (PRD M8 is not met; a disclosed deviation).
 *
 * A row the ensemble left undecided counts as an error, so the undecided count is shown beside
 * the rate rather than hidden in it.
 */
export function HumanReference({ audit }: { audit: LabelAudit }) {
  const testId = audit.test_id;

  return (
    <BenchSection
      id="human-reference"
      title="Human reference point"
      lede="How often the labelling ensemble disagreed with one person's gold label, on real rows
        nobody trained or tuned on. Every recall and false-positive rate above rests on labels at
        least this noisy."
    >
      {!testId ? (
        <EmptyNote>
          No audit yet. It appears once the human gold labels exist and{" "}
          <code className="t-mono">bench.label.audit --report test-id</code> has run.
        </EmptyNote>
      ) : (
        <>
          <p className="t-ui text-ink-2">{auditLabel(testId)}, report-only</p>
          <TableWrap label="Human reference table">
            <thead>
              <tr>
                <Th>Measure</Th>
                <Th>Value</Th>
                <Th>95% interval</Th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <Td>Ensemble label error, Clopper–Pearson</Td>
                <Td>
                  <Num>{formatPercent(testId.error_rate)}</Num>
                </Td>
                <Td>
                  <Num>{formatPercentRange(testId.cp_lo, testId.cp_hi)}</Num>
                </Td>
              </tr>
              <tr>
                <Td>Ensemble label error, server-cluster bootstrap</Td>
                <Td>
                  <Num>{formatPercent(testId.error_rate)}</Num>
                </Td>
                <Td>
                  <Num>{formatPercentRange(testId.cluster_lo, testId.cluster_hi)}</Num>
                </Td>
              </tr>
              <tr>
                <Td>Disagreements, undecided rows included</Td>
                <Td>
                  <Num>{count(testId.errors, testId.n)}</Num>
                </Td>
                <Td>
                  <span className="text-ink-2">exact count</span>
                </Td>
              </tr>
              <tr>
                <Td>Rows the ensemble left undecided</Td>
                <Td>
                  <Num>{count(testId.undecided, testId.n)}</Num>
                </Td>
                <Td>
                  <span className="text-ink-2">exact count</span>
                </Td>
              </tr>
            </tbody>
          </TableWrap>
        </>
      )}
    </BenchSection>
  );
}

function count(k: number | undefined, n: number | undefined): string {
  return typeof k === "number" && typeof n === "number" ? `${k} of ${n}` : NO_NUMBER;
}
