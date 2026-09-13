"use client";

import {
  animate,
  motion,
  useMotionValue,
  useMotionValueEvent,
  useReducedMotion,
} from "motion/react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { sieveTimeline } from "./useSieveTimeline";

export type SieveVerdict = "confirmed" | "needs_review" | "dismissed";

export type SieveDatum = {
  /** The finding id — `server/tool`. Also the tile's key. */
  id: string;
  /** What the tile is labelled with: the real tool id from the fixture. */
  label: string;
  verdict: SieveVerdict;
  /**
   * Whether the scanner flagged this tool. Extends the interface in the lane brief because the
   * golden scanner flagged 4 of 27: without it the "flagged" counter could only ever be the tile
   * count, and the hero sentence and the sieve would disagree. When no datum carries it, the
   * counter falls back to the tile count, which is the PRD's original reading.
   */
  flaggedByScanner?: boolean;
};

const TRAYS: { verdict: SieveVerdict; label: string }[] = [
  { verdict: "confirmed", label: "Confirmed" },
  { verdict: "needs_review", label: "Needs review" },
  { verdict: "dismissed", label: "Dismissed" },
];

/** How far above the line a tile starts, in pixels. Transform only. */
const DROP_PX = 56;
const EASE_OUT = [0.22, 1, 0.36, 1] as const;

type Props = {
  data: SieveDatum[];
  autoplay?: boolean;
  replayable?: boolean;
  onComplete?: () => void;
};

export function Sieve({ data, autoplay = true, replayable = true, onComplete }: Props) {
  const reduceMotion = useReducedMotion() ?? false;
  const timeline = useMemo(() => sieveTimeline(data.length), [data.length]);

  const flagged = useMemo(() => {
    const marked = data.filter((d) => d.flaggedByScanner).length;
    return marked > 0 ? marked : data.length;
  }, [data]);
  const confirmed = useMemo(() => data.filter((d) => d.verdict === "confirmed").length, [data]);

  const byTray = useMemo(
    () =>
      TRAYS.map((tray) => ({
        ...tray,
        items: data.filter((d) => d.verdict === tray.verdict),
      })),
    [data],
  );

  // A pass is identified by its number. 0 means "no pass has run", which renders the final state.
  const [pass, setPass] = useState(autoplay ? 1 : 0);
  const [settledPass, setSettledPass] = useState(0);
  const settled = reduceMotion || pass === 0 || settledPass >= pass;

  // Held in a ref so a caller's inline arrow does not restart the pass on every render. The
  // assignment runs in its own effect, declared before the one that reads it, so the ref is
  // current by the time a pass starts.
  const onCompleteRef = useRef(onComplete);
  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  const lineRef = useRef<HTMLDivElement>(null);
  const tileRefs = useRef(new Map<string, HTMLElement>());
  const [offsets, setOffsets] = useState<Map<string, number>>(new Map());

  /**
   * Each tile animates from above the sieve line down to the line, holds, then continues into
   * its tray. The tiles live in their trays in the DOM from first paint — so there is no layout
   * shift and no absolute positioning — and the distance each one has to travel back up to the
   * line is measured once per pass.
   */
  useLayoutEffect(() => {
    const line = lineRef.current?.getBoundingClientRect().top;
    if (line === undefined) return;
    const next = new Map<string, number>();
    for (const [id, element] of tileRefs.current) {
      next.set(id, line - element.getBoundingClientRect().top);
    }
    setOffsets(next);
  }, [data, pass]);

  const flaggedValue = useMotionValue(0);
  const confirmedValue = useMotionValue(0);
  const [flaggedShown, setFlaggedShown] = useState(0);
  const [confirmedShown, setConfirmedShown] = useState(0);
  useMotionValueEvent(flaggedValue, "change", (v) => setFlaggedShown(Math.round(v)));
  useMotionValueEvent(confirmedValue, "change", (v) => setConfirmedShown(Math.round(v)));

  useEffect(() => {
    if (pass === 0) return;

    if (reduceMotion) {
      // Reduced motion renders the end of the story, not a fast version of it: `settled` is
      // already true, the counters already read their final values, and the pass is over.
      onCompleteRef.current?.();
      return;
    }

    flaggedValue.set(0);
    confirmedValue.set(0);

    const lastContact = timeline.lineContactFor(Math.max(0, data.length - 1));
    const flaggedRun = animate(flaggedValue, flagged, {
      duration: Math.max(timeline.fallMs, lastContact) / 1000,
      ease: "linear",
    });
    const confirmedRun = animate(confirmedValue, confirmed, {
      duration: Math.max(1, timeline.totalMs - timeline.fallMs) / 1000,
      delay: timeline.fallMs / 1000,
      ease: "linear",
    });

    const settle = window.setTimeout(() => {
      setSettledPass(pass);
      onCompleteRef.current?.();
    }, timeline.totalMs);

    return () => {
      flaggedRun.stop();
      confirmedRun.stop();
      window.clearTimeout(settle);
    };
  }, [pass, reduceMotion, data.length, flagged, confirmed, timeline, flaggedValue, confirmedValue]);

  const registerTile = useCallback((id: string, element: HTMLElement | null) => {
    if (element) tileRefs.current.set(id, element);
    else tileRefs.current.delete(id);
  }, []);

  const replay = () => setPass((current) => current + 1);

  const indexOf = new Map(data.map((item, index) => [item.id, index]));

  return (
    <figure
      role="figure"
      aria-label="The sieve"
      data-motion={reduceMotion ? "static" : "animated"}
      className="m-0 flex flex-col gap-5"
    >
      <p className="sr-only">
        {`${flagged} findings entered the sieve. ${confirmed} were confirmed, ` +
          `${byTray[1].items.length} need review, ${byTray[2].items.length} were dismissed.`}
      </p>

      {/* The drop: empty space the tiles travel through, then the line itself. */}
      <div className="relative" aria-hidden="true">
        <div className="h-14 sm:h-20" />
        <div
          ref={lineRef}
          data-testid="sieve-line"
          className="bg-red-0 h-px w-full"
          style={{ boxShadow: "0 0 12px var(--red-glow)" }}
        />
      </div>

      <div className="flex flex-col gap-4">
        {byTray.map((tray) => (
          <section key={tray.verdict} aria-label={tray.label} className="flex flex-col gap-2">
            {/* A label, not a heading: the section already carries the accessible name, and an
                `h3` here would skip a level under the hero's `h1`. */}
            <header className="flex items-baseline justify-between gap-3">
              <p className="t-eyebrow">{tray.label}</p>
              <span
                data-testid={`sieve-tray-count-${tray.verdict}`}
                className="t-ui t-num text-ink-2"
              >
                {tray.items.length}
              </span>
            </header>

            {/*
              A fixed tray height, not a minimum: the sieve then occupies exactly the same box
              before and after the data arrives, which is what keeps the landing page's
              cumulative layout shift at zero. A full tray scrolls inside itself.
            */}
            <div className="scroll-y border-line-1 flex h-[7.5rem] flex-wrap content-start items-start gap-1.5 border-t pt-2">
              {tray.items.length === 0 ? (
                <span className="t-ui text-ink-3">None</span>
              ) : (
                tray.items.map((item) => {
                  const index = indexOf.get(item.id) ?? 0;
                  const offset = offsets.get(item.id) ?? -DROP_PX;
                  return (
                    <Tile
                      key={`${item.id}-${pass}`}
                      item={item}
                      settled={settled}
                      reduceMotion={reduceMotion}
                      offset={offset}
                      delayMs={timeline.delayFor(index)}
                      durationMs={timeline.fixedMs}
                      times={timeline.keyframeTimes}
                      register={registerTile}
                    />
                  );
                })
              )}
            </div>
          </section>
        ))}
      </div>

      <figcaption className="border-line-1 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
        <p className="t-ui text-ink-2">
          Flagged{" "}
          <span data-testid="sieve-count-flagged" className="t-num text-ink-0">
            {settled ? flagged : flaggedShown}
          </span>
          <span aria-hidden="true" className="text-ink-3 px-2">
            →
          </span>
          confirmed{" "}
          <span data-testid="sieve-count-confirmed" className="t-num text-red-0">
            {settled ? confirmed : confirmedShown}
          </span>
        </p>
        {replayable ? (
          <button type="button" onClick={replay} className="btn btn-quiet t-ui">
            Replay the pass
          </button>
        ) : null}
      </figcaption>
    </figure>
  );
}

type TileProps = {
  item: SieveDatum;
  settled: boolean;
  reduceMotion: boolean;
  offset: number;
  delayMs: number;
  durationMs: number;
  times: readonly [number, number, number, number];
  register: (id: string, element: HTMLElement | null) => void;
};

function Tile({
  item,
  settled,
  reduceMotion,
  offset,
  delayMs,
  durationMs,
  times,
  register,
}: TileProps) {
  const dismissed = item.verdict === "dismissed";
  const finalScale = dismissed ? 0.92 : 1;
  /**
   * PRD §14.4 settles dismissed tiles at 60 % opacity. That would put `--ink-3` at 2.54:1 on
   * `--bg-0` — below the AA floor PRD §14.8 requires and the contrast gate enforces — so the
   * de-emphasis is carried by the 0.92 scale and the `--ink-3` colour alone. §14.8 wins over
   * §14.4 where they disagree; see docs/handoffs/L6.md.
   */
  const finalOpacity = 1;

  const appearance = settled
    ? {
        confirmed: "bg-red-fill text-ink-0 border-red-fill",
        needs_review: "border-ink-0 text-ink-0",
        dismissed: "border-line-1 text-ink-3",
      }[item.verdict]
    : "border-line-2 text-ink-1";

  return (
    <motion.span
      ref={(element: HTMLElement | null) => register(item.id, element)}
      data-testid={`sieve-tile-${item.id}`}
      data-tray={item.verdict}
      data-settled={settled ? "true" : "false"}
      className={`t-mono u-interactive inline-flex items-center rounded-1 border px-2 py-1 text-[12px] leading-none ${appearance}`}
      initial={
        reduceMotion ? false : { y: offset - DROP_PX, opacity: 0, scale: 1 }
      }
      animate={
        reduceMotion
          ? undefined
          : {
              y: [offset - DROP_PX, offset, offset, 0],
              opacity: [0, 1, 1, finalOpacity],
              scale: [1, 1, 1, finalScale],
            }
      }
      transition={
        reduceMotion
          ? undefined
          : {
              duration: durationMs / 1000,
              delay: delayMs / 1000,
              times: [...times],
              ease: [EASE_OUT, "linear", EASE_OUT],
            }
      }
      style={reduceMotion ? { opacity: finalOpacity, scale: finalScale } : undefined}
    >
      {item.label}
    </motion.span>
  );
}
