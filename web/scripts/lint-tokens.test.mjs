import { describe, expect, it } from "vitest";

import { isLinted, lintLine, lintText } from "./lint-tokens.mjs";

/**
 * The token rule is only as good as the line check under it, and its interesting cases are the
 * negative ones — which are exactly the cases a green CI run never exercises. This file lives
 * beside the script rather than under `src/` because `scripts/` is outside the tree the script
 * itself walks: a test about raw colour literals has to contain raw colour literals.
 */

describe("lintLine", () => {
  it("rejects a raw hex colour", () => {
    expect(lintLine("  color: #ff5147;")).toEqual(["raw hex colour"]);
  });

  it("rejects a raw colour function", () => {
    expect(lintLine("  background: rgb(255 81 71 / 0.35);")).toEqual(["raw colour function"]);
  });

  it("accepts a token reference", () => {
    expect(lintLine("  color: var(--red-0);")).toEqual([]);
    expect(lintLine('  className="text-ink-0 bg-bg-1"')).toEqual([]);
  });

  it("accepts a font-family that names a token", () => {
    expect(lintLine("  font-family: var(--font-mono);")).toEqual([]);
    expect(lintLine('  style={{ fontFamily: "var(--font-display)" }}')).toEqual([]);
  });

  it("rejects a font-family literal", () => {
    expect(lintLine('  font-family: "Clash Display", sans-serif;')).toEqual([
      "font-family declaration",
    ]);
    expect(lintLine('  style={{ fontFamily: "Geist" }}')).toEqual(["fontFamily style prop"]);
  });

  it("lets the marker excuse a colour literal", () => {
    expect(lintLine("  color: #ff5147; /* lint-tokens-allow */")).toEqual([]);
  });

  it("never lets the marker excuse a font-family", () => {
    // The whole point of the marker is a value a token cannot express. A face always can.
    expect(lintLine('  font-family: "Geist"; /* lint-tokens-allow */')).toEqual([
      "font-family declaration",
    ]);
    expect(lintLine('  fontFamily: "Geist", // lint-tokens-allow')).toEqual([
      "fontFamily style prop",
    ]);
  });
});

describe("lintText", () => {
  it("reports one-based line numbers", () => {
    const found = lintText(":root {\n  --x: 1px;\n  color: #ffffff;\n}\n");
    expect(found).toEqual([{ line: 3, why: "raw hex colour", text: "color: #ffffff;" }]);
  });
});

describe("isLinted", () => {
  it("exempts the token file itself and the generated API types", () => {
    expect(isLinted("styles/tokens.css")).toBe(false);
    expect(isLinted("lib/api-types.ts")).toBe(false);
  });

  it("lints everything else it can read", () => {
    expect(isLinted("styles/globals.css")).toBe(true);
    expect(isLinted("app/layout.tsx")).toBe(true);
    expect(isLinted("styles/tokens.server.ts")).toBe(true);
  });
});
