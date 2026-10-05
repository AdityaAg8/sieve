"use client";

import Link from "next/link";

import { IconArrow } from "@/components/shell/Icons";
import { Reveal } from "@/components/shell/Reveal";
import {
  NO_NUMBER,
  NO_NUMBER_LABEL,
  formatCount,
  formatInterval,
  formatPercent,
  formatRate,
  formatRatio,
  headlineOf,
  isProvisional,
} from "@/lib/bench";
import { useBench } from "@/lib/queries";

/**
 * The four numbers the README and the write-up lead with, as a stat strip: cells
 * divided by hairlines, no boxes. Until `make bench` has run, every slot renders `—` with
 * "metric to confirm" beside it. There is no placeholder number here and there never will be:
 * an invented metric on this page would falsify the one claim the whole project is making.
 */
export function Proof() {
  const bench = useBench();
  const provisional = isProvisional(bench.data);
  const headline = headlineOf(bench.data);

  // Every note opens with the population its figure was counted over, then the comparison that
  // makes the figure readable. A piece whose key the report does not carry is left out.
  const yara = headline.baseline_fpr_real?.point;
  const dismissedN = headline.fp_dismissed_real?.n;
  const lowerBound = headline.holdout_recall_cp?.lo;
  const mcptoxClean = headline.mcptox_clean_fpr?.point;

  const slots = [
    {
      label: "Real tools sent to review",
      note: [
        "real subset, holdout; Stage 1 at T_low",
        typeof yara === "number" ? `YARA flags ${formatPercent(yara)} of the same tools` : null,
      ],
      value: formatInterval(headline.real_review_share),
    },
    {
      label: "YARA false positives dismissed",
      note: [
        "real tools YARA wrongly flagged, holdout" +
          (typeof dismissedN === "number" ? `, n = ${dismissedN}` : ""),
      ],
      value: formatInterval(headline.fp_dismissed_real),
    },
    {
      label: "Synthetic attacks caught",
      note: [
        "synthetic holdout" +
          (typeof lowerBound === "number"
            ? `; exact 95% lower bound ${formatRatio(lowerBound, 3)}`
            : ""),
      ],
      value: formatCount(headline.holdout_recall_cp),
    },
    {
      label: "Real attacks not dismissed",
      note: [
        "MCPTox, evaluation-only" +
          (typeof mcptoxClean === "number"
            ? `; Sieve flags ${formatPercent(mcptoxClean, 0)} of MCPTox's clean tools`
            : ""),
      ],
      value: formatRate(headline.real_poisoned_recall),
    },
  ];

  return (
    <section aria-labelledby="proof-heading" className="band band-alt">
      <div className="wrap flex flex-col gap-10">
        <Reveal className="flex flex-wrap items-end justify-between gap-6">
          <div className="flex max-w-[640px] flex-col gap-3">
            <p className="eyebrow-rail with-mark">Measured · not asserted</p>
            <h2 id="proof-heading" className="t-h2 text-ink-0">
              What the numbers say
            </h2>
            {provisional ? (
              <p className="t-body text-ink-2 max-w-[62ch]">
                Numbers appear after the first <code className="t-mono text-ink-1">make bench</code>.
                Until then every slot below reads {NO_NUMBER} — this page will not show a number
                nobody has measured.
              </p>
            ) : (
              <p className="t-body text-ink-2 max-w-[62ch]">
                Every interval is a 95% cluster bootstrap over servers, regenerated from committed
                predictions. The LLM judge is deferred to v1.1, so uncertain tools go to human
                review rather than to a verdict, and each figure names the tools it counts.
              </p>
            )}
          </div>
          <Link href="/benchmark" className="link-arrow">
            Read the full benchmark
            <IconArrow size={16} />
          </Link>
        </Reveal>

        <Reveal as="dl" stagger={90} className="stat-strip">
          {slots.map((slot) => (
            // A `<dl>`'s `div` may only wrap `dt` and `dd`, so the qualifier lives inside the
            // term rather than in a sibling paragraph — and the term comes first in the DOM, as
            // the spec requires. `flex-col-reverse` puts the figure on top where the eye wants it.
            <div key={slot.label} className="stat-cell reveal flex-col-reverse">
              <dt className="t-ui text-ink-0">
                {slot.label}
                <span className="t-ui text-ink-2 mt-1 block font-normal">
                  {slot.value === NO_NUMBER ? NO_NUMBER_LABEL : slot.note.filter(Boolean).join(". ")}
                </span>
              </dt>
              <dd
                className={`stat-figure mb-2 ${slot.value === NO_NUMBER ? "text-ink-3" : "text-ink-0"}`}
              >
                {slot.value}
              </dd>
            </div>
          ))}
        </Reveal>
      </div>
    </section>
  );
}
