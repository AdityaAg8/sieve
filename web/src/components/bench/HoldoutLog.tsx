import { NO_NUMBER, type HoldoutLogRow } from "@/lib/bench";

import { BenchSection, Num, TableWrap, Td, Th } from "./primitives";

/**
 * `bench/results/test_opened.log`, every line of it (`bench.eval_final.append_log_line`).
 *
 * Publishing the log is the point. "We opened the holdout once" is a claim; an append-only log
 * with a git SHA and a reason per line is something a reader can check. A short log is the
 * result; a long one would be the finding.
 */
export function HoldoutLog({ rows }: { rows: HoldoutLogRow[] }) {
  return (
    <BenchSection
      id="holdout-log"
      title="Holdout log"
      lede="The holdout was opened this many times."
    >
      <p className="stat-figure text-ink-0">
        <Num>{String(rows.length)}</Num>
      </p>

      {rows.length === 0 ? (
        <p className="t-body text-ink-1">
          Not yet opened. The sealed holdout stays sealed until{" "}
          <code className="t-mono">bench/eval_final.py</code> runs under{" "}
          <code className="t-mono">SIEVE_FINAL=1</code>, which appends a line here.
        </p>
      ) : (
        <TableWrap label="Holdout log table">
          <thead>
            <tr>
              <Th>Commit</Th>
              <Th>Opened (UTC)</Th>
              <Th>Rows</Th>
              <Th>Reason</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.git_sha}-${row.utc}`} data-testid="holdout-log-row">
                <Td>
                  <span className="t-mono">{row.git_sha.slice(0, 12)}</span>
                </Td>
                <Td>
                  <span className="t-mono t-num">{row.utc}</span>
                </Td>
                <Td>
                  <Num>
                    {typeof row.rows_evaluated === "number" ? String(row.rows_evaluated) : NO_NUMBER}
                  </Num>
                </Td>
                <Td>{row.reason}</Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </BenchSection>
  );
}
