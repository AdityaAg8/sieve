"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatPercent, type BreakdownRow } from "@/lib/bench";

import { EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/** What a recall cell says for a bucket that holds no attacks: undefined, not zero. */
export const NOT_APPLICABLE = "n/a";

/** Recall is undefined where there are no poisoned rows, or where the report gives no recall. */
function hasRecall(row: BreakdownRow): row is BreakdownRow & { recall: number } {
  return row.n_poisoned !== 0 && typeof row.recall === "number";
}

/**
 * The chart's data. A bucket with no recall carries `null`, which Recharts leaves undrawn; `0`
 * would draw a zero-height bar and read as "caught none of them".
 */
export function breakdownSeries(rows: BreakdownRow[]) {
  return rows.map((row) => ({
    bucket: row.bucket,
    recall: hasRecall(row) ? row.recall : null,
    fpr: row.fpr ?? 0,
    n: row.n ?? 0,
  }));
}

/**
 * Two series, both proportions on one 0–1 axis, so one axis is enough — there is never a second
 * y-scale on this page.
 *
 * The palette is forced by the token contract (PRD §14.2): `--red-0` is the only chromatic
 * colour in the app and is reserved for Sieve's own series, so the second series is `--ink-2`.
 * `scripts/validate_palette.js` passes CVD separation (ΔE 11.5 protan), the normal-vision floor
 * (ΔE 21.6) and contrast against `--bg-1`; it fails the chroma-floor and lightness-band checks,
 * which is inherent to a deliberately achromatic-plus-one-signal palette. The secondary encoding
 * the skill requires in that case is present and permanent: a legend, and the same numbers as a
 * table directly below every chart.
 */
export function BreakdownChart({
  id,
  title,
  lede,
  bucketLabel,
  rows,
}: {
  id: string;
  title: string;
  lede: string;
  bucketLabel: string;
  rows: BreakdownRow[];
}) {
  const data = breakdownSeries(rows);

  return (
    <section aria-labelledby={id} className="border-line-1 flex min-w-0 flex-col gap-4 border-t pt-10">
      {/* One step below the page title, like every other section seam. See `primitives.tsx`. */}
      <h2 id={id} className="t-h3 text-ink-0">
        {title}
      </h2>
      <p className="t-body text-ink-1 max-w-[68ch]">{lede}</p>

      {data.length === 0 ? (
        <EmptyNote>
          No breakdown yet. It appears with the first <code className="t-mono">make bench</code>.
        </EmptyNote>
      ) : (
        <>
          {/* The chart is the picture; the table below it is the data. Only one of them needs to
                be in the accessibility tree, and it is the table. */}
          <div
            aria-hidden="true"
            data-red="series"
            className="h-72 w-full min-w-0 overflow-hidden"
            data-testid={`${id}-chart`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data} margin={{ top: 8, right: 8, bottom: 24, left: 8 }} barGap={2}>
                <CartesianGrid stroke="var(--line-1)" vertical={false} />
                <XAxis
                  dataKey="bucket"
                  stroke="var(--line-2)"
                  tick={{ fill: "var(--ink-2)", fontSize: 12 }}
                  label={{
                    value: bucketLabel,
                    position: "insideBottom",
                    offset: -12,
                    fill: "var(--ink-2)",
                    fontSize: 12,
                  }}
                />
                <YAxis
                  domain={[0, 1]}
                  stroke="var(--line-2)"
                  tick={{ fill: "var(--ink-2)", fontSize: 12 }}
                  tickFormatter={(value: number) => formatPercent(value, 0)}
                  label={{
                    value: "Proportion",
                    angle: -90,
                    position: "insideLeft",
                    fill: "var(--ink-2)",
                    fontSize: 12,
                  }}
                />
                <Tooltip
                  cursor={{ fill: "var(--bg-2)" }}
                  contentStyle={{
                    background: "var(--bg-2)",
                    border: "1px solid var(--line-2)",
                    borderRadius: "var(--r-1)",
                    color: "var(--ink-0)",
                  }}
                  formatter={(value, name) => [
                    formatPercent(typeof value === "number" ? value : undefined),
                    String(name),
                  ]}
                />
                <Legend
                  wrapperStyle={{ color: "var(--ink-2)", fontSize: 12, paddingTop: 8 }}
                  iconType="square"
                />
                <Bar dataKey="recall" name="Sieve recall" radius={[4, 4, 0, 0]}>
                  {data.map((row) => (
                    <Cell key={row.bucket} fill="var(--red-0)" />
                  ))}
                </Bar>
                <Bar dataKey="fpr" name="False-positive rate" radius={[4, 4, 0, 0]}>
                  {data.map((row) => (
                    <Cell key={row.bucket} fill="var(--ink-2)" />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <TableWrap label={`${title} table`}>
            <thead>
              <tr>
                <Th>{bucketLabel}</Th>
                <Th>Sieve recall</Th>
                <Th>False-positive rate</Th>
                <Th>Rows</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.bucket}>
                  <Td>
                    <span data-testid={`${id}-bucket`}>{row.bucket}</span>
                  </Td>
                  <Td>
                    {hasRecall(row) ? (
                      <Num>{formatPercent(row.recall)}</Num>
                    ) : (
                      <span
                        className="t-num text-ink-2"
                        title="No poisoned tools in this bucket, so recall is undefined"
                      >
                        {NOT_APPLICABLE}
                      </span>
                    )}
                  </Td>
                  <Td>
                    <Num>{formatPercent(row.fpr)}</Num>
                  </Td>
                  <Td>
                    <Num>{typeof row.n === "number" ? String(row.n) : "—"}</Num>
                  </Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        </>
      )}
    </section>
  );
}
