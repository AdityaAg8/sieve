// Fails if any source file other than tokens.css contains a raw colour or font-family.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

// fileURLToPath, not `.pathname`: on Windows `.pathname` yields "/D:/..." and the hand-rolled
// regex strip breaks on any path containing an escaped character.
const ROOT = fileURLToPath(new URL("../src", import.meta.url));
const ALLOW = new Set(["styles/tokens.css"]);
// A font-family/fontFamily declaration is only a violation when its value isn't a token
// reference — `var(--font-*)` is the only permitted value anywhere outside tokens.css.
const PATTERNS = [
  [/#[0-9a-fA-F]{3,8}\b/, "raw hex colour"],
  [/\b(rgb|rgba|hsl|hsla|oklch|oklab)\(/, "raw colour function"],
  [/font-family\s*:(?!\s*var\(--font-)/, "font-family declaration"],
  [/\bfontFamily\s*:(?!\s*["'`]?var\(--font-)/, "fontFamily style prop"],
];
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
