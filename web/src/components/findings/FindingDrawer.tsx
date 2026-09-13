"use client";

import { useEffect, useRef } from "react";

import { Evidence } from "@/components/evidence/Evidence";
import type { Finding } from "@/lib/api";
import { evidenceBlocks } from "@/lib/evidence";
import { OWASP_MCP_TOP_10 } from "@/lib/links";
import { owaspTitle, subLabelTitle } from "@/lib/owasp";
import { CLASS_LABEL, STAGE_LABEL, formatProbability, stageNote } from "@/lib/verdict";

import { DismissalReason } from "./DismissalReason";
import { Feedback } from "./Feedback";
import { VerdictChip } from "./VerdictChip";

type Props = {
  finding: Finding | null;
  judgeEnabled: boolean | null;
  onClose: () => void;
};

/**
 * The detail view, as a modal `<dialog>` so Esc, the focus trap and the backdrop are the
 * browser's rather than ours. Every string taken from the analysed tool is rendered as text in
 * a monospace block; nothing here is ever interpreted as markup.
 */
export function FindingDrawer({ finding, judgeEnabled, onClose }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (finding && !dialog.open) dialog.showModal();
    if (!finding && dialog.open) dialog.close();
  }, [finding]);

  return (
    <dialog
      ref={ref}
      aria-label={finding ? `Finding ${finding.id}` : "Finding"}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
      className="bg-bg-1 border-line-2 text-ink-1 ml-auto h-full max-h-full w-[min(46rem,100vw)] max-w-full border-l p-0 backdrop:bg-bg-0/80"
    >
      {finding ? <FindingDetail finding={finding} judgeEnabled={judgeEnabled} onClose={onClose} /> : null}
    </dialog>
  );
}

export function FindingDetail({
  finding,
  judgeEnabled,
  onClose,
}: {
  finding: Finding;
  judgeEnabled: boolean | null;
  onClose?: () => void;
}) {
  const { result, tool } = finding;
  const scanner = result.scanner_findings[0];
  const note = stageNote(result);
  const blocks = evidenceBlocks(tool, result.evidence_spans);

  return (
    <article className="flex h-full flex-col gap-6 overflow-y-auto p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <p className="t-eyebrow">{finding.server_name ?? "Tool"}</p>
          <h2 className="t-mono text-ink-0 break-all">{finding.id}</h2>
        </div>
        <div className="flex items-center gap-3">
          <VerdictChip verdict={result.verdict} />
          {onClose ? (
            <button type="button" onClick={onClose} className="btn btn-quiet">
              Close
            </button>
          ) : null}
        </div>
      </header>

      <section aria-label="Definition" className="flex flex-col gap-4">
        <h3 className="t-eyebrow">Definition</h3>
        {blocks.length === 0 ? (
          <p className="quote p-3">No description.</p>
        ) : (
          blocks.map((block) => (
            <div key={block.label} className="flex flex-col gap-2">
              <p className="t-mono text-ink-2 break-all">{block.label}</p>
              <Evidence
                text={block.text}
                spans={block.spans}
                field={block.field}
                path={block.path}
              />
            </div>
          ))
        )}
      </section>

      <section aria-label="Verdicts" className="grid gap-6 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <h3 className="t-eyebrow">Scanner said</h3>
          {scanner ? (
            <p className="t-ui text-ink-1">
              <span className="t-mono">{scanner.scanner}</span>
              {scanner.rule_id ? (
                <>
                  {" · "}
                  <span className="t-mono">{scanner.rule_id}</span>
                </>
              ) : null}
              {scanner.severity ? ` · ${scanner.severity}` : null}
              {scanner.message ? <span className="text-ink-2 block">{scanner.message}</span> : null}
            </p>
          ) : (
            <p className="t-ui text-ink-2">No finding</p>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <h3 className="t-eyebrow">Sieve said</h3>
          {result.verdict === "dismissed" ? (
            <DismissalReason result={result} />
          ) : (
            <p className="t-ui t-num text-ink-1">
              {CLASS_LABEL[result.predicted_class]} (p ={" "}
              {formatProbability(result.class_probabilities?.[result.predicted_class])})
            </p>
          )}
          {note ? <p className="t-ui text-ink-2">{note}</p> : null}
        </div>
      </section>

      {result.reasoning ? (
        <section aria-label="Reasoning" className="flex flex-col gap-2">
          <h3 className="t-eyebrow">Reasoning</h3>
          <p className="t-body text-ink-1">{result.reasoning}</p>
        </section>
      ) : null}

      {result.remediation ? (
        <section aria-label="Remediation" className="flex flex-col gap-2">
          <h3 className="t-eyebrow">Remediation</h3>
          <p className="t-body text-ink-1">{result.remediation}</p>
        </section>
      ) : null}

      {result.sub_labels.length > 0 || result.owasp.length > 0 ? (
        <section aria-label="Classification" className="flex flex-col gap-3">
          <h3 className="t-eyebrow">Classification</h3>
          <ul className="flex flex-wrap gap-2">
            {result.sub_labels.map((label) => (
              <li key={label} className="t-ui text-ink-1 border-line-2 rounded-1 border px-2 py-1">
                {subLabelTitle(label)}
              </li>
            ))}
            {result.owasp.map((id) => (
              <li key={id}>
                <a
                  href={OWASP_MCP_TOP_10}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="t-ui u-interactive text-ink-1 hover:text-ink-0 border-line-2 inline-block rounded-1 border px-2 py-1"
                >
                  {id} · {owaspTitle(id)}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <Feedback findingId={finding.id} />

      <section aria-label="Provenance" className="border-line-1 mt-auto border-t pt-4">
        <dl className="t-ui grid gap-x-6 gap-y-1 sm:grid-cols-2">
          <Row term="Stage" value={STAGE_LABEL[result.stage]} />
          <Row term="Model" value={result.model_version} />
          <Row term="Judge" value={result.judge_model ?? (judgeEnabled === false ? "Disabled on this deployment" : "Not used")} />
          <Row term="Latency" value={`${result.latency_ms} ms`} />
        </dl>
      </section>
    </article>
  );
}

function Row({ term, value }: { term: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-ink-2">{term}</dt>
      <dd className="t-num text-ink-1 text-right">{value}</dd>
    </div>
  );
}
