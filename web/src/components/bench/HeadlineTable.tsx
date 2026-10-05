import {
  NO_NUMBER,
  formatCount,
  formatInterval,
  formatPercent,
  formatRate,
  formatRatio,
  type Headline,
} from "@/lib/bench";

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

/**
 * M1 is the rate of real tools the whole pipeline confirms as poisoned. The judge that would
 * confirm them is deferred to v1.1, so there is no such number to show.
 */
export const M1_NOT_MEASURED = "not measured: judge deferred (v1.1)";

/** A test cell: either a real result, "no test defined", or an honest gap. */
type TestCell = { text: string; unmeasured?: boolean };

/** McNemar's p belongs to the all-negatives comparison it was computed on, and only to that. */
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

/** YARA's rate on the same real tools, set beside the review share so it can be read against it. */
function yaraOnSameTools(headline: Headline): TestCell {
  const yara = headline.baseline_fpr_real;
  if (!yara || typeof yara.point !== "number") return { text: NO_NUMBER, unmeasured: true };
  const counts = formatCount(yara);
  const share = formatPercent(yara.point);
  const of = counts === NO_NUMBER ? share : `${share} (${counts})`;
  return { text: `YARA flags ${of} of the same tools` };
}

/** The exact lower bound on the synthetic recall, to three places as the README prints it. */
function exactLowerBound(headline: Headline): TestCell {
  const lo = headline.holdout_recall_cp?.lo;
  if (typeof lo !== "number") return { text: NO_NUMBER, unmeasured: true };
  return { text: `Exact 95% lower bound ${formatRatio(lo, 3)}` };
}

function mcptoxCleanFlagged(headline: Headline): TestCell {
  const point = headline.mcptox_clean_fpr?.point;
  if (typeof point !== "number") return { text: NO_NUMBER, unmeasured: true };
  return { text: `Sieve flags ${formatPercent(point, 0)} of MCPTox's clean tools` };
}

/**
 * The count of real tools Stage 1 auto-confirms. A rate of exactly zero is written as the count
 * it stands for (`0/105`); any other rate is printed as the interval the report carries, because
 * the page never divides.
 */
function autoConfirmed(headline: Headline): string {
  const real = headline.fpr_pipeline_real;
  if (real && real.point === 0 && typeof real.n === "number") return `0/${real.n}`;
  return formatInterval(real);
}

type Row = {
  metric: string;
  slice: string;
  /** Rendered as the value cell; `text` is plain prose, not a figure. */
  value: string;
  prose?: boolean;
  tests: TestCell[];
};

/**
 * Every figure comes from the sealed report (`holdout.json`, training plan H3), read at the
 * pre-registered headline model's own thresholds, and every row names the population it was
 * counted over. The product's false-positive figure is the share of real tools sent to review;
 * the pipeline's own rate is not measured, because the judge is deferred.
 *
 * "YARA false positives dismissed" appears twice, on two populations, and the real-only one
 * comes first. The McNemar p-value was computed on the second, so it sits there and nowhere else.
 */
export function HeadlineTable({ headline }: { headline: Headline }) {
  const dismissedN = headline.fp_dismissed_real?.n;
  const rows: Row[] = [
    {
      metric: "Real tools sent to review",
      slice: "real subset, holdout; Stage 1 at T_low",
      value: formatInterval(headline.real_review_share),
      tests: [yaraOnSameTools(headline)],
    },
    {
      metric: "YARA false positives dismissed",
      slice: `real tools YARA wrongly flagged, holdout${typeof dismissedN === "number" ? ` (n = ${dismissedN})` : ""}`,
      value: formatInterval(headline.fp_dismissed_real),
      tests: [{ text: NO_TEST_DEFINED }],
    },
    {
      metric: "YARA false positives dismissed",
      slice: "all holdout negatives, real and synthetic",
      value: formatInterval(headline.fp_reduction),
      tests: [mcnemar(headline)],
    },
    {
      metric: "Pipeline false-positive rate (M1)",
      slice: "real subset, holdout",
      value: M1_NOT_MEASURED,
      prose: true,
      tests: [{ text: NO_TEST_DEFINED }],
    },
    {
      metric: "Real tools auto-confirmed",
      slice: "Stage 1 only, real subset, holdout (review band excluded)",
      value: autoConfirmed(headline),
      tests: [{ text: NO_TEST_DEFINED }],
    },
    {
      metric: "Synthetic attacks caught",
      slice: "synthetic holdout attacks, recall at T_low",
      value: formatCount(headline.holdout_recall_cp),
      tests: [exactLowerBound(headline), releaseGate(headline)],
    },
    {
      metric: "Real attacks not dismissed",
      slice: "real poisoned, MCPTox (evaluation-only), Stage 1 at T_low",
      value: formatRate(headline.real_poisoned_recall),
      tests: [mcptoxCleanFlagged(headline)],
    },
  ];

  return (
    <BenchSection
      id="headline"
      title="Headline"
      lede="Every interval is a 95% cluster bootstrap resampling servers, not rows — the unit of
        independence here is the server, and resampling rows would report an interval narrower
        than the evidence supports. The LLM judge is deferred to v1.1, so no tool is confirmed
        beyond Stage 1: a tool in the uncertain band is routed to review, and the share of real
        tools sent to review is the false-positive figure. Each row names the tools it counts."
    >
      <TableWrap label="Headline numbers table">
        <thead>
          <tr>
            <Th>Metric</Th>
            <Th>Slice</Th>
            <Th>Value [95% CI]</Th>
            <Th>Test, bound or gate</Th>
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
                {row.prose ? (
                  <span className="t-ui text-ink-2">{row.value}</span>
                ) : (
                  <Num>{row.value}</Num>
                )}
              </Td>
              <Td>
                {/* `Num` is the page's one treatment for an unmeasured figure: `—`, muted, with
                    "metric to confirm" on hover and to a screen reader. A defined-but-absent
                    test is a different statement and gets plain words instead. */}
                <div className="flex flex-col gap-1">
                  {row.tests.map((test) =>
                    test.unmeasured ? (
                      <Num key={test.text}>{test.text}</Num>
                    ) : (
                      <span key={test.text} className="t-ui text-ink-2">
                        {test.text}
                      </span>
                    ),
                  )}
                </div>
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </BenchSection>
  );
}
