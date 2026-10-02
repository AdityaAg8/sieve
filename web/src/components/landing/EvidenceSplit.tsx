"use client";

import { ConfidenceBar } from "@/components/findings/ConfidenceBar";
import { VerdictChip } from "@/components/findings/VerdictChip";
import { Evidence } from "@/components/evidence/Evidence";
import { IconCheck } from "@/components/shell/Icons";
import { Reveal } from "@/components/shell/Reveal";
import { demoScanId } from "@/lib/config";
import { evidenceBlocks } from "@/lib/evidence";
import { subLabelTitle } from "@/lib/owasp";
import { useFindings } from "@/lib/queries";
import { CLASS_LABEL, formatProbability } from "@/lib/verdict";

const POINTS = [
  {
    lead: "Every verdict quotes the span it read.",
    sub: "Marked in the tool's own text, with the tool's own words. A judge that cannot quote does not get to decide.",
  },
  {
    lead: "The scanner's verdict stays beside Sieve's.",
    sub: "Both columns on every row, so you can disagree with either.",
  },
  {
    lead: "A dismissal states its class and its probability.",
    sub: "Dismissed is grey and never called safe: Sieve has evidence for “not this finding”, not for “this tool is fine”.",
  },
] as const;

/**
 * The evidence, shown on a real finding rather than described. The card on the left is the
 * first confirmed finding of the sample scan, rendered read-only — the same fields, the same
 * quote, the same mark the results screen paints. Nothing in it is typed into this component.
 */
export function EvidenceSplit() {
  const findings = useFindings(demoScanId());
  const finding = findings.data?.items.find((item) => item.result.verdict === "confirmed") ?? null;

  return (
    <section aria-labelledby="evidence-heading" className="band band-alt">
      <div className="wrap">
        <div className="split reverse">
          <Reveal direction="right" className="flex flex-col gap-6">
            <div className="flex flex-col gap-3">
              <p className="eyebrow-rail with-mark">Evidence · every verdict</p>
              <h2 id="evidence-heading" className="t-h2 text-ink-0 max-w-[20ch]">
                A verdict you can check, not one you have to trust.
              </h2>
            </div>
            <ul className="checklist">
              {POINTS.map((point) => (
                <li key={point.lead}>
                  <span className="tick" aria-hidden="true">
                    <IconCheck size={13} />
                  </span>
                  {point.lead}
                  <span className="sub">{point.sub}</span>
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal direction="left">
            {finding ? (
              <article
                aria-label={`Finding ${finding.id}, as the results screen shows it`}
                className="panel flex flex-col gap-4 p-5 sm:p-6"
              >
                <div className="panel-head">
                  <p className="t-eyebrow">{finding.server_name ?? "Tool"}</p>
                  <VerdictChip verdict={finding.result.verdict} />
                </div>

                <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
                  <p className="t-mono text-ink-0 break-all">{finding.id}</p>
                  <ConfidenceBar
                    value={finding.result.confidence}
                    tone={finding.result.verdict === "confirmed" ? "red" : "ink"}
                  />
                </div>

                {evidenceBlocks(finding.tool, finding.result.evidence_spans)
                  .filter((block) => block.spans.length > 0)
                  .slice(0, 1)
                  .map((block) => (
                    <div key={block.label} className="flex flex-col gap-2">
                      <p className="t-mono text-ink-2 break-all">{block.label}</p>
                      <Evidence
                        text={block.text}
                        spans={block.spans}
                        field={block.field}
                        path={block.path}
                      />
                    </div>
                  ))}

                <dl className="border-line-1 grid gap-x-6 gap-y-3 border-t pt-4 sm:grid-cols-2">
                  <div className="flex flex-col gap-1">
                    <dt className="t-eyebrow">Scanner said</dt>
                    <dd className="t-ui text-ink-1">
                      {finding.result.scanner_findings[0] ? (
                        <span className="t-mono">{finding.result.scanner_findings[0].scanner}</span>
                      ) : (
                        <span className="text-ink-2">No finding</span>
                      )}
                    </dd>
                  </div>
                  <div className="flex flex-col gap-1">
                    <dt className="t-eyebrow">Sieve said</dt>
                    <dd className="t-ui t-num text-ink-1">
                      {CLASS_LABEL[finding.result.predicted_class]} (p ={" "}
                      {formatProbability(
                        finding.result.class_probabilities?.[finding.result.predicted_class],
                      )}
                      )
                    </dd>
                  </div>
                </dl>

                {finding.result.sub_labels.length > 0 ? (
                  <ul className="flex flex-wrap gap-1.5">
                    {finding.result.sub_labels.map((label) => (
                      <li
                        key={label}
                        className="t-ui text-ink-2 border-line-2 rounded-1 border px-1.5 py-0.5"
                      >
                        {subLabelTitle(label)}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </article>
            ) : (
              <div aria-hidden="true" className="panel flex flex-col gap-4 p-6">
                <div className="panel-head">
                  <span className="bg-bg-2 h-3 w-24 rounded-1" />
                  <span className="bg-bg-2 h-6 w-24 rounded-1" />
                </div>
                <span className="bg-bg-2 h-4 w-56 rounded-1" />
                <span className="bg-bg-2 h-24 w-full rounded-1" />
                <span className="bg-bg-2 h-4 w-2/3 rounded-1" />
              </div>
            )}
          </Reveal>
        </div>
      </div>
    </section>
  );
}
