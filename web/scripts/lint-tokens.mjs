// Fails if any source file other than tokens.css contains a raw colour or font-family.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// fileURLToPath, not `.pathname`: on Windows `.pathname` yields "/D:/..." and the hand-rolled
// regex strip breaks on any path containing an escaped character.
//
// Resolved when the command runs, not when the module loads: the unit test beside this file
// imports the rules below through Vite, which rewrites `import.meta.url` to something
// `fileURLToPath` refuses.
const defaultRoot = () => fileURLToPath(new URL("../src", import.meta.url));
const ALLOW = new Set(["styles/tokens.css", "lib/api-types.ts"]);
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

/**
 * What is wrong with one line, if anything.
 *
 * `lint-tokens-allow` exempts a colour literal a line genuinely cannot express as a token — and
 * nothing else. A font-family always can be `var(--font-*)`, so no marker excuses one; letting
 * the same comment cover both would make the marker a way to smuggle a face onto the page.
 */
export function lintLine(line) {
  const exempt = line.includes("lint-tokens-allow");
  const reasons = [];
  for (const [re, why] of COLOUR_PATTERNS) if (!exempt && re.test(line)) reasons.push(why);
  for (const [re, why] of FONT_PATTERNS) if (re.test(line)) reasons.push(why);
  return reasons;
}

/** Every violation in a file's text, as `{ line, why }` with 1-based line numbers. */
export function lintText(text) {
  return text.split("\n").flatMap((line, i) =>
    lintLine(line).map((why) => ({ line: i + 1, why, text: line.trim() })),
  );
}

/** Whether this file is linted at all. `rel` is POSIX-separated and relative to `src`. */
export function isLinted(rel) {
  return !ALLOW.has(rel) && /\.(tsx?|css|mdx?)$/.test(rel);
}

function sourceFiles(root) {
  const files = [];
  (function walk(dir) {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(tsx?|css|mdx?)$/.test(name)) files.push(p);
    }
  })(root);
  return files;
}

export function main(root = defaultRoot()) {
  const files = sourceFiles(root);
  let failures = 0;
  for (const f of files) {
    const rel = relative(root, f).replace(/\\/g, "/");
    if (!isLinted(rel)) continue;
    for (const { line, why, text } of lintText(readFileSync(f, "utf8"))) {
      console.error(`${rel}:${line}: ${why}: ${text}`);
      failures++;
    }
  }
  if (failures) {
    console.error(`\n${failures} token violation(s).`);
    return 1;
  }
  console.log(`tokens ok (${files.length} files)`);
  return 0;
}

// Run only when invoked as a command, so the unit test can import the rules above.
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exit(main());
}
