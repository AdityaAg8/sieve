/**
 * The sieve's timing, computed from the tile count.
 *
 * PRD §14.4 fixes three things: a 28 ms stagger, an 80 ms pause on the line, and a total of at
 * most 2.4 s for the 27-tool sample. Nothing here is a magic total: `totalMs` is derived, and if
 * a scan is large enough that a 28 ms stagger would overrun the budget, the stagger compresses
 * rather than the budget stretching. A scan may hold up to 500 tools (PRD §13.2), so that case
 * is real.
 */

/** Fall from above the line to the line. */
export const FALL_MS = 520;
/** The beat on the line: the moment of reading. */
export const PAUSE_MS = 80;
/** Line to tray. */
export const SORT_MS = 420;
/** The stagger the PRD names. */
export const STAGGER_MS = 28;
/** The ceiling the PRD names, for any tile count. */
export const MAX_TOTAL_MS = 2400;

export type SieveTimeline = {
  /** Tiles the timeline was built for. */
  count: number;
  /** Per-tile entry delay, in milliseconds. */
  staggerMs: number;
  fallMs: number;
  pauseMs: number;
  sortMs: number;
  /** Everything that is not stagger: one tile's own journey. */
  fixedMs: number;
  /** Wall-clock duration of the whole pass. */
  totalMs: number;
  delayFor: (index: number) => number;
  lineContactFor: (index: number) => number;
  settleFor: (index: number) => number;
  /** Keyframe positions inside one tile's animation, for Motion's `times`. */
  keyframeTimes: [number, number, number, number];
};

export function sieveTimeline(count: number): SieveTimeline {
  const fixedMs = FALL_MS + PAUSE_MS + SORT_MS;
  const budgetMs = Math.max(0, MAX_TOTAL_MS - fixedMs);
  const staggerMs = count > 1 ? Math.min(STAGGER_MS, budgetMs / (count - 1)) : 0;
  const spanMs = staggerMs * Math.max(0, count - 1);

  const delayFor = (index: number) => index * staggerMs;

  return {
    count,
    staggerMs,
    fallMs: FALL_MS,
    pauseMs: PAUSE_MS,
    sortMs: SORT_MS,
    fixedMs,
    totalMs: spanMs + fixedMs,
    delayFor,
    lineContactFor: (index) => delayFor(index) + FALL_MS,
    settleFor: (index) => delayFor(index) + fixedMs,
    keyframeTimes: [0, FALL_MS / fixedMs, (FALL_MS + PAUSE_MS) / fixedMs, 1],
  };
}
