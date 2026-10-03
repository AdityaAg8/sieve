import {
  NO_NUMBER,
  formatRatio,
  type AuditBlock,
  type EnsembleAgreement,
  type LabelAudit,
} from "@/lib/bench";

import { BenchSection, Num, TableWrap, Td, Th } from "./primitives";

/** The size of the sealed audit by design (training plan §4.3), used until the file says. */
export const AUDIT_ROWS = 104;

/** "human vs ensemble, 104-row sealed audit" — the one label both panels use (finding F73). */
export function auditLabel(audit: AuditBlock | undefined): string {
  return `human vs ensemble, ${audit?.n ?? AUDIT_ROWS}-row sealed audit`;
}

/** The three labeller pairs `bench.label.vote` scores, in its order. */
const LABELLER_PAIRS = ["sonnet~qwen", "sonnet~gemma", "qwen~gemma"];

function ciNote(lo: number | null | undefined, hi: number | null | undefined): string {
  const range =
    typeof lo === "number" && typeof hi === "number"
      ? `[${formatRatio(lo)}, ${formatRatio(hi)}]`
      : NO_NUMBER;
  return `95% CI ${range}, server-cluster bootstrap`;
}

/**
 * v1 has no second human annotator: PRD M8 (second-human κ ≥ 0.70) is not met, and it is
 * replaced — as a disclosed deviation — by Cohen's κ between one person's gold labels and the
 * labelling ensemble's on a random 104-row sample of the sealed real rows (`label_audit.json`,
 * training plan F3). The panel says exactly that and never calls the ensemble an annotator.
 *
 * Checker-vs-generator agreement on synthetic rows is reported beside it and is deliberately
 * **not** called κ: the generator wrote the payload and therefore knows the answer, so it is not
 * an independent rater and the number is not an inter-rater statistic.
 */
export function Agreement({
  audit,
  ensemble,
}: {
  audit: LabelAudit;
  ensemble: EnsembleAgreement | undefined;
}) {
  const testId = audit.test_id;
  const dev50 = audit.dev50;
  const pairs = ensemble?.pairwise_kappa ?? {};
  const pairNames = Object.keys(pairs).length > 0 ? Object.keys(pairs) : LABELLER_PAIRS;

  return (
    <BenchSection
      id="agreement"
      title="Agreement"
      lede="Human vs ensemble, 104-row sealed audit. There is no second human annotator in v1:
        one person's gold labels on a random sample of the sealed real rows are compared with the
        labelling ensemble's. The audit is report-only — it never changed a label, a threshold or
        a model."
    >
      <dl className="stat-strip">
        <Stat
          term="κ, human vs ensemble"
          value={formatRatio(testId?.kappa)}
          note={auditLabel(testId)}
          detail={ciNote(testId?.kappa_lo, testId?.kappa_hi)}
        />
        <Stat
          term="κ, dev-50 gate"
          value={formatRatio(dev50?.kappa)}
          note={`human vs ensemble on 50 dev rows; proceed at κ ≥ ${formatRatio(dev50?.gate ?? 0.7)}`}
          detail={ciNote(dev50?.kappa_lo, dev50?.kappa_hi)}
        />
        <Stat
          term="Checker-vs-generator agreement"
          value={formatRatio(ensemble?.synthetic?.checker_agreement_round0)}
          note="synthetic rows — not an inter-rater κ, the generator knew the answer"
        />
      </dl>

      <TableWrap label="Labeller agreement table">
        <thead>
          <tr>
            <Th>Labeller pair</Th>
            <Th>Cohen&rsquo;s κ</Th>
            <Th>Rows</Th>
          </tr>
        </thead>
        <tbody>
          {pairNames.map((name) => (
            <tr key={name}>
              <Td>{name.replace("~", " ~ ")}</Td>
              <Td>
                <Num>{formatRatio(pairs[name]?.kappa)}</Num>
              </Td>
              <Td>
                <Num>{typeof pairs[name]?.n === "number" ? String(pairs[name]?.n) : NO_NUMBER}</Num>
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </BenchSection>
  );
}

function Stat({
  term,
  value,
  note,
  detail,
}: {
  term: string;
  value: string;
  note: string;
  detail?: string;
}) {
  return (
    // A `<dl>`'s `div` may only wrap `dt` and `dd`, and the term comes first in the DOM;
    // `flex-col-reverse` puts the figure on top without reordering the markup.
    <div className="stat-cell flex-col-reverse">
      <dt className="t-ui text-ink-1">
        <span data-testid="agreement-term">{term}</span>
        <span className="t-ui text-ink-2 mt-1 block font-normal">{note}</span>
        {detail ? (
          <span className="t-ui text-ink-2 mt-1 block font-normal">{detail}</span>
        ) : null}
      </dt>
      <dd className="stat-figure">
        <Num>{value}</Num>
      </dd>
    </div>
  );
}
