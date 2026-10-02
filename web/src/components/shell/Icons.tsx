/**
 * Inline glyphs. One weight, one grid: 24 × 24, `stroke="currentColor"`, 1.7 px, round caps
 * and joins. No icon font, no sprite, nothing external. Every one is decorative — the text
 * beside it carries the meaning — so every one is `aria-hidden`.
 */
type IconProps = { size?: number; className?: string };

function Svg({ size = 20, className = "", children }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={className}
    >
      {children}
    </svg>
  );
}

export function IconCheck(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 12.5l4.2 4.2L19 7.5" />
    </Svg>
  );
}

export function IconArrow(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 12h14M13 6l6 6-6 6" />
    </Svg>
  );
}

/** A file being read, for the CLI, a report, or a dump. */
export function IconFile(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h6" />
    </Svg>
  );
}

/** A terminal prompt. */
export function IconTerminal(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="4.5" width="18" height="15" rx="2" />
      <path d="M7 9l3.5 3L7 15M12.5 15H17" />
    </Svg>
  );
}

/** A branch and a check: a build gate. */
export function IconGate(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="6" cy="18" r="2.2" />
      <circle cx="18" cy="9" r="2.2" />
      <path d="M6 8.2v7.6M18 11.2c0 3-3 4-6 4H8.5" />
    </Svg>
  );
}

/** A shield with a mesh line through it: an exported finding. */
export function IconExport(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z" />
      <path d="M8 12h8" />
    </Svg>
  );
}

/** A server. */
export function IconServer(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="4" y="4" width="16" height="6.5" rx="1.5" />
      <rect x="4" y="13.5" width="16" height="6.5" rx="1.5" />
      <path d="M8 7.25h.01M8 16.75h.01" />
    </Svg>
  );
}

/** A magnifier over a line: a scanner. */
export function IconScan(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="10.5" cy="10.5" r="6" />
      <path d="M15 15l5 5M7.5 10.5h6" />
    </Svg>
  );
}

/** Three lines in, one line out: a filter. */
export function IconSieve(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 6h16M7 12h10M10 18h4" />
    </Svg>
  );
}

/** A quoted span. */
export function IconQuote(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 5h16M4 10h10M4 15h16M4 20h7" />
      <path d="M16 9.5l4 2.5-4 2.5" />
    </Svg>
  );
}

/** A lock: nothing runs. */
export function IconLock(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="5" y="10.5" width="14" height="10" rx="2" />
      <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
    </Svg>
  );
}

/** A clock: retention. */
export function IconClock(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </Svg>
  );
}

/** A key: the keyboard. */
export function IconKeys(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M7 10h.01M11 10h.01M15 10h.01M7 14h10" />
    </Svg>
  );
}

/** Menu and close, for the capsule on a narrow screen. */
export function IconMenu(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 7h16M4 12h16M4 17h16" />
    </Svg>
  );
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Svg>
  );
}
