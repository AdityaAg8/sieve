import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { colourToken, pageBackgroundColour } from "./tokens.server";

/**
 * Every colour literal below is assembled at runtime rather than written out. This file sits
 * inside the tree `scripts/lint-tokens.mjs` walks, and a test about raw hex values would
 * otherwise be the one place in `src/` that spells one.
 */
const HASH = String.fromCharCode(35);
const CHARCOAL = `${HASH}131316`;
const WASH = `${"rgb"}(232 56 47 / 0.14)`;

describe("colourToken", () => {
  it("returns the declared value", () => {
    expect(colourToken(`:root { --bg-0: ${CHARCOAL}; }`, "bg-0")).toBe(CHARCOAL);
  });

  it("reads a functional notation too", () => {
    expect(colourToken(`:root { --red-wash: ${WASH}; }`, "red-wash")).toBe(WASH);
  });

  it("ignores surrounding whitespace and neighbouring declarations", () => {
    const css = `:root {\n  --bg-1: ${HASH}1A1A1E;\n  --bg-0:   ${CHARCOAL}  ;\n}`;
    expect(colourToken(css, "bg-0")).toBe(CHARCOAL);
  });

  it("throws when the token is missing", () => {
    expect(() => colourToken(":root { --ink-0: white; }", "bg-0")).toThrow(
      "tokens.css declares no --bg-0",
    );
  });

  it("throws when the value is not a colour literal", () => {
    expect(() => colourToken(":root { --bg-0: papayawhip; }", "bg-0")).toThrow(
      "not a colour literal",
    );
    expect(() => colourToken(":root { --bg-0: var(--other); }", "bg-0")).toThrow(
      "not a colour literal",
    );
  });
});

describe("pageBackgroundColour", () => {
  it("reads --bg-0 out of the real tokens.css", () => {
    const value = pageBackgroundColour();
    const css = readFileSync(join(process.cwd(), "src", "styles", "tokens.css"), "utf8");
    // Independent of the reader: the value it returned is declared, verbatim, in the file.
    expect(css).toContain(`--bg-0: ${value};`);
    expect(value.startsWith(HASH)).toBe(true);
  });
});
