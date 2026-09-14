import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Reads a design token out of `tokens.css` on the server.
 *
 * `tokens.css` is the only place a colour is spelled (PRD §14.2), and the rule has no exceptions.
 * A `<meta name="theme-color">` content attribute is the one consumer that cannot take
 * `var(--bg-0)` — it wants a literal — so the literal is *read* from the token file rather than
 * copied beside it. There is then still exactly one place the value lives, and a change to
 * `--bg-0` moves the browser's chrome with it.
 *
 * Server-only, and named so. There is no `server-only` package in this project's dependency tree,
 * so the `.server.ts` suffix and `node:fs` are what mark it: importing this from a client
 * component fails the build rather than shipping the reader to a browser.
 */

/** Next builds and serves from `web/`, which is where the token file's path is rooted. */
const TOKENS_PATH = join(process.cwd(), "src", "styles", "tokens.css");

/** The notations `tokens.css` actually uses. Anything else is a mistake worth failing on. */
const COLOUR_FUNCTIONS = ["rgb", "rgba", "hsl", "hsla", "oklch", "oklab"];
const COLOUR_LITERAL = new RegExp(
  `^(?:#[0-9a-fA-F]{3,8}|(?:${COLOUR_FUNCTIONS.join("|")})\\([^()]*\\))$`,
);

/**
 * The value of `--<name>` in the CSS given, asserted to be a colour literal.
 *
 * Takes the stylesheet as text rather than reading it, so the failure paths are testable without
 * a temporary file. Throws rather than falling back: a silent default here would be a colour
 * nobody chose, painted on the browser's own chrome, and it would never be noticed.
 */
export function colourToken(css: string, name: string): string {
  const declaration = new RegExp(`--${name}\\s*:\\s*([^;]+);`).exec(css);
  if (!declaration) {
    throw new Error(`tokens.css declares no --${name}`);
  }
  const value = declaration[1].trim();
  if (!COLOUR_LITERAL.test(value)) {
    throw new Error(`--${name} is "${value}", which is not a colour literal a <meta> can carry`);
  }
  return value;
}

/**
 * `--bg-0`, for `viewport.themeColor` in the root layout. Read once per server process: a missing
 * or malformed token then fails the build (every prerendered route evaluates this) rather than
 * one request at a time.
 */
export function pageBackgroundColour(): string {
  return colourToken(readFileSync(TOKENS_PATH, "utf8"), "bg-0");
}
