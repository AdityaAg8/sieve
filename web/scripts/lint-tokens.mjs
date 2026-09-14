// Fails if any source file other than tokens.css contains a raw colour or font-family.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

// fileURLToPath, not `.pathname`: on Windows `.pathname` yields "/D:/..." and the hand-rolled
// regex strip breaks on any path containing an escaped character.
const ROOT = fileURLToPath(new URL("../src", import.meta.url));
const ALLOW = new Set(["styles/tokens.css"]);

// The one escape hatch, and it is checked rather than trusted: a line may carry
// `lint-tokens-allow: mirrors --<token>` where a literal is unavoidable — a `<meta>` content
// attribute cannot hold `var(--bg-0)` — and every hex on that line must then equal the token.
const TOKENS_CSS = fileURLToPath(new URL("../src/styles/tokens.css", import.meta.url));
const TOKEN_VALUES = new Map(
  [...readFileSync(TOKENS_CSS, "utf8").matchAll(/--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)]
    .map(([, name, hex]) => [name, hex.toLowerCase()]),
);
const MIRROR = /lint-tokens-allow:\s*mirrors\s+--([a-z0-9-]+)/;
// A font-family/fontFamily declaration is only a violation when its value isn't a token
// reference — `var(--font-*)` is the only permitted value anywhere outside tokens.css.
const COLOUR_PATTERNS = [
  [/#[0-9a-fA-F]{3,8}\b/, "raw hex colour"],
  [/\b(rgb|rgba|hsl|hsla|oklch|oklab)\(/, "raw colour function"],
];
const FONT_PATTERNS = [
  [/font-family\s*:(?!\s*var\(--font-)/, "font-family declaration"],
  [/\bfontFamily\s*:(?!\s*["'`]?var\(--font-)/, "fontFamily style prop"],
];
const PATTERNS = [...COLOUR_PATTERNS, ...FONT_PATTERNS];
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(tsx?|css|mdx?)$/.test(name)) files.push(p);
  }
})(ROOT);
let failures = 0;
for (const f of files) {
  const rel = relative(ROOT, f).replace(/\\/g, "/");
  if (ALLOW.has(rel) || rel === "lib/api-types.ts") continue;
  const lines = readFileSync(f, "utf8").split("\n");
  lines.forEach((line, i) => {
    const mirror = MIRROR.exec(line);
    if (mirror) {
      const token = mirror[1];
      const expected = TOKEN_VALUES.get(token);
      const literals = [...line.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map(([hex]) => hex.toLowerCase());
      if (expected === undefined) {
        console.error(`${rel}:${i + 1}: mirrors --${token}, which tokens.css does not define`);
        failures++;
      } else if (literals.length === 0 || literals.some((hex) => hex !== expected)) {
        console.error(
          `${rel}:${i + 1}: claims to mirror --${token} (${expected}) but spells ${literals.join(", ") || "no literal"}`,
        );
        failures++;
      }
      // The hatch covers the colour literal it declares, and nothing else: a `font-family` on
      // the same line is still a violation.
      for (const [re, why] of FONT_PATTERNS) {
        if (re.test(line)) {
          console.error(`${rel}:${i + 1}: ${why}: ${line.trim()}`);
          failures++;
        }
      }
      return;
    }
    for (const [re, why] of PATTERNS) {
      if (re.test(line) && !line.includes("lint-tokens-allow")) {
        console.error(`${rel}:${i + 1}: ${why}: ${line.trim()}`);
        failures++;
      }
    }
  });
}
if (failures) { console.error(`\n${failures} token violation(s).`); process.exit(1); }
console.log(`tokens ok (${files.length} files)`);
