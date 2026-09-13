import type { TriageResult } from "@/lib/api";
import { dismissalReason } from "@/lib/verdict";

/**
 * Grey, and it says which class it decided on. Never green: a dismissal is evidence about this
 * finding, not a clean bill of health for the tool.
 */
export function DismissalReason({
  result,
  className = "",
}: {
  result: TriageResult;
  className?: string;
}) {
  const reason = dismissalReason(result);
  if (!reason) return null;
  return <p className={`t-ui t-num text-ink-3 ${className}`}>{reason}</p>;
}
