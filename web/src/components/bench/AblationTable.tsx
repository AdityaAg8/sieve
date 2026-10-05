import { formatInterval, formatMeanStd, type AblationRow } from "@/lib/bench";

import { BenchSection, DeferredChip, EmptyNote, Num, TableWrap, Td, Th } from "./primitives";

/** How many seeds a row's numbers rest on, in words. */
function seedsOf(row: AblationRow): { text: string; muted: boolean } {
  if (row.single_seed) return { text: "single seed", muted: false };
  const n = row.seeds?.length ?? 0;
  // A keyword row (the scanner, TF-IDF, the hygiene detector) has no seed dimension at all.
  if (n > 1) return { text: `${n} seeds`, muted: false };
  return { text: "not seeded", muted: true };
}

function noteOf(row: AblationRow): string {
  const parts: string[] = [];
  const deferred = row.deferred_breakdown_of ?? [];
  if (deferred.length > 0) {
    const names = deferred.map((id) => `row ${id}`).join(", ");
    parts.push(`The judge half (${names}) is deferred to v1.1 (D8).`);
  }
  if (row.note) parts.push(row.note);
  return parts.join(" ");
}

/**
 * The rows of PRD §11.5 as `bench.ablations.run` writes them (training plan H5), all on pooled
 * out-of-fold predictions. Encoder rows report the mean across seeds with the sample standard
 * deviation; a row marked single seed ran once by design (D9). The judge rows are emitted as
 * deferred (D8) and render that way: grey, with the reason, and no number.
 */
export function AblationTable({ rows }: { rows: AblationRow[] }) {
  return (
    <BenchSection
      id="ablations"
      title="Ablations"
      lede="Every scored row is computed on pooled out-of-fold predictions from the 5-fold grouped
        cross-validation over development servers; none of them has seen the holdout. Encoder
        rows give the mean across seeds ± the sample standard deviation. Development holds no real
        attack, so recall is measured on synthetic attacks only; the review column counts real
        tools not auto-dismissed, which for the scanner row is simply what it flags. Rows that
        need the LLM judge are listed as deferred: the judge moves to v1.1, and a deferred row
        has no number rather than a zero."
    >
      {rows.length === 0 ? (
        <EmptyNote>
          No ablations yet. They appear with the first <code className="t-mono">make bench</code>.
        </EmptyNote>
      ) : (
        <TableWrap label="Ablations table">
          <thead>
            <tr>
              <Th>#</Th>
              <Th>Configuration</Th>
              <Th>Recall at T_low [CI]</Th>
              <Th>Real tools sent to review [CI]</Th>
              <Th>Macro-F1 (mean ± sd)</Th>
              <Th>Seeds</Th>
              <Th>Note</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) =>
              row.status === "deferred" ? (
                <tr
                  key={row.row}
                  data-testid="ablation-row"
                  data-row={row.row}
                  data-status="deferred"
                >
                  <Td>
                    <span className="t-num text-ink-3">{row.row}</span>
                  </Td>
                  <Td>
                    <span className="text-ink-3">{row.name}</span>
                  </Td>
                  <Td colSpan={4}>
                    <span className="flex flex-wrap items-baseline gap-2">
                      <DeferredChip />
                      <span className="text-ink-3">{row.reason ?? "Not scored in v1."}</span>
                    </span>
                  </Td>
                  <Td>
                    <span className="text-ink-3">{row.note ?? ""}</span>
                  </Td>
                </tr>
              ) : (
                <tr key={row.row} data-testid="ablation-row" data-row={row.row} data-status="active">
                  <Td>
                    <Num>{String(row.row)}</Num>
                  </Td>
                  <Td>{row.name}</Td>
                  <Td>
                    <Num>{formatInterval(row.oof_recall_at_t_low)}</Num>
                  </Td>
                  <Td>
                    <Num>{formatInterval(row.fpr_stage1_real_at_t_low)}</Num>
                  </Td>
                  <Td>
                    <Num>{formatMeanStd(row.macro_f1_mean, row.macro_f1_std)}</Num>
                  </Td>
                  <Td>
                    <SeedCell row={row} />
                  </Td>
                  <Td>
                    <span className="text-ink-2">{noteOf(row)}</span>
                  </Td>
                </tr>
              ),
            )}
          </tbody>
        </TableWrap>
      )}
    </BenchSection>
  );
}

function SeedCell({ row }: { row: AblationRow }) {
  const seeds = seedsOf(row);
  return (
    <span className={`whitespace-nowrap ${seeds.muted ? "text-ink-3" : "text-ink-1"}`}>
      {seeds.text}
    </span>
  );
}
