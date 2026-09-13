import { formatProbability } from "@/lib/verdict";

/**
 * The number is always spelled out beside the bar: a length alone is not a value anyone can
 * quote in a review. Tabular figures keep the column from shifting as rows stream in.
 */
export function ConfidenceBar({ value, tone = "ink" }: { value: number; tone?: "ink" | "red" }) {
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <div className="flex w-32 shrink-0 items-center gap-2">
      <div
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={Number(clamped.toFixed(2))}
        aria-valuetext={formatProbability(clamped)}
        className="bg-bg-2 border-line-1 h-1.5 flex-1 overflow-hidden rounded-1 border"
      >
        <div
          className={`h-full origin-left ${tone === "red" ? "bg-red-fill" : "bg-ink-2"}`}
          style={{ transform: `scaleX(${clamped})` }}
        />
      </div>
      <span className="t-ui t-num text-ink-1 w-8 text-right">{formatProbability(clamped)}</span>
    </div>
  );
}
