"use client";

/**
 * What a route says when a request failed.
 *
 * Every failure gets one of these, and none of them is allowed to look like a result. The
 * distinction matters most on the results screen: "no findings" and "we could not read the scan"
 * render identically if you are careless, and the first sentence is a clean bill of health the
 * app has not earned. Sentence case, says what happened, and the control is a verb.
 */
export function ErrorNote({
  title,
  detail,
  onRetry,
  className = "",
}: {
  title: string;
  detail?: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div role="alert" className={`surface flex flex-col items-start gap-3 p-6 ${className}`}>
      <p className="t-body text-ink-0">{title}</p>
      {detail ? <p className="t-ui text-ink-2 max-w-[62ch]">{detail}</p> : null}
      {onRetry ? (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}
