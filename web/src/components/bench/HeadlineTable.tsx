import { formatInterval, formatRatio, type Headline } from "@/lib/bench";

import { BenchSection, Num, TableWrap, Td, Th } from "./primitives";

/**
 * The real-subset figure first, the synthetic-inclusive figure second and labelled as such — the
 * order is the claim. The McNemar p-value sits beside the reduction figure rather than in a
 * footnote, because a reduction without a significance test is a number, not a result.
 */
export function HeadlineTable({ headline }: { headline: Headline }) {
  const rows = [
    {
      metric: "Pipeline false-positive rate",
      slice: "real subset (headline)",
      value: formatInterval(headline.fpr_pipeline_real),
      test: "—",
    },
    {
      metric: "Pipeline false-positive rate",
      slice: "synthetic-inclusive",
      value: formatInterval(headline.fpr_pipeline_all),
      test: "—",
    },
    {
      metric: "False positives removed",
      slice: headline.baseline_name ? `vs ${headline.baseline_name}` : "vs the scanner baseline",
      value: formatInterval(headline.fp_reduction_vs_baseline),
      test:
        typeof headline.mcnemar_p === "number"
          ? `McNemar p = ${formatRatio(headline.mcnemar_p, 4)}`
          : "—",
    },
    {
      metric: "Recall",
      slice: "sealed holdout",
      value: formatInterval(headline.holdout_recall),
      test: "—",
    },
    {
      metric: "Recall",
      slice: "real poisoned (evaluation-only)",
      value: formatInterval(headline.real_poisoned_recall),
      test: "—",
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
                <Num>{row.test}</Num>
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </BenchSection>
  );
}
