/**
 * How a verdict is put into words.
 *
 * PRD §14.6: a dismissal states its class. Dismissed is grey, never green, because Sieve has
 * proven "not this finding" — it has not proven the tool is safe. The probability shown is
 * always `class_probabilities[predicted_class]`, computed here to two decimals; none of these
 * strings is ever assembled from a hardcoded number.
 */
import type { PredictedClass, Stage, TriageResult, Verdict } from "./api";

export const VERDICT_LABEL: Record<Verdict, string> = {
  confirmed: "Confirmed",
  needs_review: "Needs review",
  dismissed: "Dismissed",
};

export const VERDICT_ORDER: Verdict[] = ["confirmed", "needs_review", "dismissed"];

export const CLASS_LABEL: Record<PredictedClass, string> = {
  poisoned: "Poisoned",
  imperative_benign: "Imperative benign",
  benign: "Benign",
};

/** Two decimals, always — "0.9" would read as a different number. */
export function formatProbability(value: number | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
}

export function classProbability(result: TriageResult): number | undefined {
  return result.class_probabilities?.[result.predicted_class];
}

/**
 * The dismissal sentence, or `null` when the finding was not dismissed. The three shapes are
 * fixed by PRD §14.6 and the lane brief; only the number moves.
 */
export function dismissalReason(result: TriageResult): string | null {
  if (result.verdict !== "dismissed") return null;
  const p = formatProbability(classProbability(result));

  switch (result.predicted_class) {
    case "imperative_benign":
      return `Dismissed — legitimate imperative instruction (p = ${p})`;
    case "benign":
      return `Dismissed — descriptive documentation (p = ${p})`;
    case "poisoned":
      // The classifier said poisoned and the judge disagreed, so the judge owns this one.
      return `Dismissed by judge — p(poisoned) = ${p}`;
  }
}

/**
 * What a `needs_review` verdict means when the stage explains it. A bare stage string
 * ("judge_budget_exhausted") is a database value, not a sentence for a person.
 */
export function stageNote(result: TriageResult): string | null {
  const notes: Partial<Record<Stage, string>> = {
    judge_budget_exhausted: "Needs review — the daily judge budget was exhausted",
    judge_failed: "Needs review — the judge did not return a usable answer",
  };
  return notes[result.stage] ?? null;
}

/** How the verdict was reached, for the detail view. */
export const STAGE_LABEL: Record<Stage, string> = {
  classifier: "Decided by the classifier",
  judge: "Escalated to the judge",
  judge_failed: "Judge failed",
  judge_budget_exhausted: "Judge budget exhausted",
};
