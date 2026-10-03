import { NO_NUMBER, formatInterval, formatRatio, type Headline } from "@/lib/bench";

import { BenchSection, Num, TableWrap, Td, Th } from "./primitives";

/**
 * What a row says when the benchmark defines no significance test for it — a single rate on a
 * single slice is not a paired comparison, so there is nothing to test.
 *
 * Deliberately not `—`. `—` is this project's one word for "nobody has measured this yet", and a
 * reader who cannot tell the two apart will read "no test defined" as "the test is missing", or
 * read a genuinely unmeasured p-value as a design decision. `GET /bench/summary` carries
 * `mcnemar_p` only once `make bench` has run a paired comparison, so both states are real and
 * both are on this page.
 */
export const NO_TEST_DEFINED = "No test defined";

/** A test cell: either a real result, "no test defined", or an honest gap. */
type TestCell = { text: string; unmeasured?: boolean };

function mcnemar(headline: Headline): TestCell {
  if (typeof headline.mcnemar_p === "number") {
    return { text: `McNemar p = ${formatRatio(headline.mcnemar_p, 4)}` };
  }
  return { text: NO_NUMBER, unmeasured: true };
}

function releaseGate(headline: Headline): TestCell {
  if (typeof headline.release_gate_passed !== "boolean") {
    return { text: NO_NUMBER, unmeasured: true };
  }
  return { text: headline.release_gate_passed ? "Release gate passed" : "Release gate not met" };
}

/**
 * Every figure comes from the sealed report (`holdout.json`, training plan H3), read at the
 * pre-registered headline model's own thresholds. The real-subset figure first — the order is
 * the claim. The McNemar p-value sits beside the reduction figure rather than in a footnote,
 * because a reduction without a significance test is a number, not a result.
 */
export function HeadlineTable({ headline }: { headline: Headline }) {
  const rows: { metric: string; slice: string; value: string; test: TestCell }[] = [
    {
      metric: "Pipeline false-positive rate",
      slice: "real subset, test-ID (headline)",
      value: formatInterval(headline.fpr_pipeline_real),
      test: { text: NO_TEST_DEFINED },
    },
    {
      metric: "Stage-1 false-positive rate at T_low",
      slice: "real subset, test-ID",
      value: formatInterval(headline.fpr_stage1_real),
      test: { text: NO_TEST_DEFINED },
    },
    {
      metric: "False positives removed (relative)",
      slice: "vs YARA, Sieve as a triage layer",
      value: formatInterval(headline.fp_reduction),
      test: mcnemar(headline),
    },
    {
      metric: "Recall at T_low",
      slice: "holdout-synthetic (release gate)",
      value: formatInterval(headline.holdout_recall),
      test: releaseGate(headline),
    },
    {
      metric: "Stage-1 recall at T_low",
      slice: "real poisoned, MCPTox (evaluation-only)",
      value: formatInterval(headline.real_poisoned_recall),
      test: { text: NO_TEST_DEFINED },
    },
  ];

  return (
    <BenchSection
      id="headline"
      title="Headline"
      lede="Every interval is a 95% cluster bootstrap resampling servers, not rows — the unit of
        independence here is the server, and resampling rows would report an interval narrower
        than the evidence supports. The LLM judge is deferred to v1.1, so pipeline figures are
        Stage 1 alone: a tool in the uncertain band is routed to review, never confirmed."
    >
      <TableWrap label="Headline numbers table">
        <thead>
          <tr>
            <Th>Metric</Th>
            <Th>Slice</Th>
            <Th>Value [95% CI]</Th>
            <Th>Test or gate</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.metric}-${row.slice}`}>
              <Td>{row.metric}</Td>
              <Td>
                <span className="text-ink-2">{row.slice}</span>
              </Td>
              <Td>
                <Num>{row.value}</Num>
              </Td>
              <Td>
                {/* `Num` is the page's one treatment for an unmeasured figure: `—`, muted, with
                    "metric to confirm" on hover and to a screen reader. A defined-but-absent
                    test is a different statement and gets plain words instead. */}
                {row.test.unmeasured ? (
                  <Num>{row.test.text}</Num>
                ) : (
                  <span className="t-ui text-ink-2">{row.test.text}</span>
                )}
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </BenchSection>
  );
}
