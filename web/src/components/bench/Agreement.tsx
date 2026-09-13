import { formatRatio, type Agreement as AgreementData } from "@/lib/bench";

import { BenchSection, Num, TableWrap, Td, Th } from "./primitives";

/**
 * κ three ways — overall, real subset (the headline, because that is where the boundary lives),
 * and per class as observed agreement plus Gwet's AC1, which is robust to the prevalence skew
 * that makes κ misleading on a rare class.
 *
 * Blind-vs-generator agreement is reported here too and is deliberately **not** called κ: the
 * generator wrote the payload and therefore knows the answer, so it is not an independent rater
 * and the number is not an inter-rater statistic. Mislabelling it would be the single most
 * flattering mistake this page could make.
 */
export function Agreement({ agreement }: { agreement: AgreementData | undefined }) {
  const perClass = agreement?.per_class ?? [];

  return (
    <BenchSection
      id="agreement"
      title="Agreement"
      lede="How much two people agreed about the labels, and — separately — how often a blind
        annotator landed on the class the generator intended."
    >
      <dl className="grid gap-6 sm:grid-cols-3">
        <Stat
          term="κ, real subset"
          value={formatRatio(agreement?.real_subset)}
          note="the headline: the boundary lives here"
        />
        <Stat term="κ, overall" value={formatRatio(agreement?.overall)} note="all labelled rows" />
        <Stat
          term="Blind-vs-generator agreement"
          value={formatRatio(agreement?.blind_vs_generator)}
          note="not an inter-rater κ — the generator knew the answer"
        />
      </dl>

      <TableWrap>
        <thead>
          <tr>
            <Th>Class</Th>
            <Th>Observed agreement</Th>
            <Th>Gwet&rsquo;s AC1</Th>
          </tr>
        </thead>
        <tbody>
          {(perClass.length > 0
            ? perClass
            : [
                { class: "benign" },
                { class: "imperative_benign" },
                { class: "poisoned" },
              ]
          ).map((row) => (
            <tr key={row.class}>
              <Td>{row.class}</Td>
              <Td>
                <Num>{formatRatio((row as { observed?: number }).observed)}</Num>
              </Td>
              <Td>
                <Num>{formatRatio((row as { ac1?: number }).ac1)}</Num>
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </BenchSection>
  );
}

function Stat({ term, value, note }: { term: string; value: string; note: string }) {
  return (
    <div className="rail flex flex-col gap-1 pl-5">
      <dd className="t-h2 t-num text-ink-0">
        <Num>{value}</Num>
      </dd>
      <dt className="t-ui text-ink-1">{term}</dt>
      <p className="t-ui text-ink-2">{note}</p>
    </div>
  );
}
