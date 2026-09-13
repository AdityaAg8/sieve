// Fails if any (foreground, background) token pair the app uses misses WCAG AA.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const TOKENS = fileURLToPath(new URL("../src/styles/tokens.css", import.meta.url));
const css = readFileSync(TOKENS, "utf8");

const tokens = {};
for (const [, name, hex] of css.matchAll(/--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g)) {
  tokens[name] = hex;
}

// [foreground, background, minimum ratio, why]
const PAIRS = [
  ["ink-0", "bg-0", 4.5, "headings on the page"],
  ["ink-0", "bg-1", 4.5, "headings on a surface"],
  ["ink-0", "bg-2", 4.5, "headings on a raised surface"],
  ["ink-0", "bg-3", 4.5, "headings on a selected row"],
  ["ink-1", "bg-0", 4.5, "body on the page"],
  ["ink-1", "bg-1", 4.5, "body on a surface"],
  ["ink-1", "bg-2", 4.5, "body on a raised surface"],
  ["ink-1", "bg-3", 4.5, "body on a selected row"],
  ["ink-2", "bg-0", 4.5, "labels on the page"],
  ["ink-2", "bg-1", 4.5, "labels on a surface"],
  ["ink-2", "bg-2", 4.5, "labels on a raised surface"],
  ["ink-3", "bg-0", 4.5, "dismissed rows on the page"],
  ["ink-3", "bg-1", 4.5, "dismissed rows on a surface"],
  ["red-0", "bg-0", 4.5, "confirmed text on the page"],
  ["red-0", "bg-1", 4.5, "confirmed text on a surface"],
  ["ink-0", "red-fill", 4.5, "chip label on a filled confirmed chip"],
  ["line-2", "bg-0", 3.0, "strong hairline as a UI border"],
  // Added by lane L6, one row per new (foreground, background) pair the app paints.
  ["red-0", "bg-2", 4.5, "evidence text and marks on a raised quote surface"],
  ["red-fill", "bg-0", 3.0, "the confirmed chip's filled square as a UI element"],
  ["ink-0", "red-1", 4.5, "button label on the pressed primary fill"],
  ["focus", "bg-0", 3.0, "the 2 px focus ring on the page"],
  ["focus", "bg-1", 3.0, "the 2 px focus ring on a surface"],
];

const channel = (c) => {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
};
const luminance = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
};
const ratio = (a, b) => {
  const [l1, l2] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
};

let failures = 0;
for (const [fg, bg, min, why] of PAIRS) {
  if (!tokens[fg] || !tokens[bg]) {
    console.error(`missing token: --${fg} or --${bg}`);
    failures++;
    continue;
  }
  const r = ratio(tokens[fg], tokens[bg]);
  const line = `--${fg} on --${bg}: ${r.toFixed(2)}:1 (need ${min}:1) — ${why}`;
  if (r < min) { console.error(`FAIL ${line}`); failures++; } else { console.log(`ok   ${line}`); }
}
if (failures) { console.error(`\n${failures} contrast failure(s).`); process.exit(1); }
console.log(`contrast ok (${PAIRS.length} pairs)`);
