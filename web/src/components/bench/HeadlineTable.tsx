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

/**
 * The real-subset figure first, the synthetic-inclusive figure second and labelled as such — the
 * order is the claim. The McNemar p-value sits beside the reduction figure rather than in a
 * footnote, because a reduction without a significance test is a number, not a result.
 */
export function HeadlineTable({ headline }: { headline: Headline }) {
  const rows: { metric: string; slice: string; value: string; test: TestCell }[] = [
    {
      metric: "Pipeline false-positive rate",
      slice: "real subset (headline)",
      value: formatInterval(headline.fpr_pipeline_real),
      test: { text: NO_TEST_DEFINED },
    },
    {
      metric: "Pipeline false-positive rate",
      slice: "synthetic-inclusive",
      value: formatInterval(headline.fpr_pipeline_all),
      test: { text: NO_TEST_DEFINED },
    },
    {
      metric: "False positives removed",
      slice: headline.baseline_name ? `vs ${headline.baseline_name}` : "vs the scanner baseline",
      value: formatInterval(headline.fp_reduction_vs_baseline),
      test: mcnemar(headline),
    },
    {
      metric: "Recall",
      slice: "sealed holdout",
      value: formatInterval(headline.holdout_recall),
      test: { text: NO_TEST_DEFINED },
    },
    {
      metric: "Recall",
      slice: "real poisoned (evaluation-only)",
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
        than the evidence supports."
    >
      <TableWrap>
        <thead>
          <tr>
            <Th>Metric</Th>
            <Th>Slice</Th>
            <Th>Value [95% CI]</Th>
            <Th>Test</Th>
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
