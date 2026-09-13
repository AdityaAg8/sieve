"use client";

import Link from "next/link";

import {
  NO_NUMBER,
  NO_NUMBER_LABEL,
  formatInterval,
  headlineOf,
  isProvisional,
} from "@/lib/bench";
import { useBench } from "@/lib/queries";

/**
 * The four numbers the README and the write-up lead with (PRD §4.3). Until `make bench` has run,
 * every slot renders `—` with "metric to confirm" beside it. There is no placeholder number here
 * and there never will be: an invented metric on this page would falsify the one claim the whole
 * project is making.
 */
export function Proof() {
  const bench = useBench();
  const provisional = isProvisional(bench.data);
  const headline = headlineOf(bench.data);

  const slots = [
    {
      label: "Pipeline false-positive rate",
      note: "real subset, 95% cluster bootstrap",
      value: formatInterval(headline.fpr_pipeline_real),
    },
    {
      label: "False positives removed",
      note: headline.baseline_name
        ? `vs ${headline.baseline_name}, paired bootstrap`
        : "vs the scanner baseline, paired bootstrap",
      value: formatInterval(headline.fp_reduction_vs_baseline),
    },
    {
      label: "Recall on the sealed holdout",
      note: "opened once, logged",
      value: formatInterval(headline.holdout_recall),
    },
    {
      label: "Recall on real poisoned tools",
      note: "evaluation-only slice",
      value: formatInterval(headline.real_poisoned_recall),
    },
  ];

  return (
    <section
      aria-labelledby="proof-heading"
      className="border-line-1 mx-auto max-w-[84rem] border-t px-4 py-16 sm:px-8"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h2 id="proof-heading" className="t-h2 text-ink-0">
          What the numbers say
        </h2>
        <Link
          href="/benchmark"
          className="t-ui u-interactive text-ink-1 hover:text-ink-0 border-line-2 border-b pb-0.5"
        >
          Read the full benchmark
        </Link>
      </div>

      {provisional ? (
        <p className="t-body text-ink-2 mt-4 max-w-[62ch]">
          Numbers appear after the first <code className="t-mono text-ink-1">make bench</code>.
          Until then every slot below reads {NO_NUMBER} — this page will not show a number nobody
          has measured.
        </p>
      ) : null}

      <dl className="mt-10 grid gap-8 sm:grid-cols-2 xl:grid-cols-4">
        {slots.map((slot) => (
          // A `<dl>`'s `div` may only wrap `dt` and `dd`, so the qualifier lives inside the
          // term rather than in a sibling paragraph.
          <div key={slot.label} className="rail flex flex-col gap-2 pl-5">
            <dd
              className={`t-display t-num text-[40px] leading-none ${
                slot.value === NO_NUMBER ? "text-ink-3" : "text-ink-0"
              }`}
            >
              {slot.value}
            </dd>
            <dt className="t-ui text-ink-1">
              {slot.label}
              <span className="t-ui text-ink-2 mt-1 block font-normal">
                {slot.value === NO_NUMBER ? NO_NUMBER_LABEL : slot.note}
              </span>
            </dt>
          </div>
        ))}
      </dl>
    </section>
  );
}
