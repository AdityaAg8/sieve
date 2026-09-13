import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";

import { server } from "@/mocks/server";

// Every request a test makes is served by the MSW handlers in `src/mocks/handlers.ts`.
// `onUnhandledRequest: "error"` is deliberate: a component that reaches a route nobody
// mocked should fail the test rather than quietly resolve against the network.
/**
 * jsdom ships no `matchMedia`, and Motion's `useReducedMotion` needs one. Tests that care about
 * reduced motion call `setPrefersReducedMotion(true)` before rendering.
 */
let prefersReducedMotion = false;

export function setPrefersReducedMotion(value: boolean): void {
  prefersReducedMotion = value;
}

window.matchMedia = ((query: string) => ({
  matches: query.includes("prefers-reduced-motion") ? prefersReducedMotion : false,
  media: query,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
})) as typeof window.matchMedia;

beforeEach(() => setPrefersReducedMotion(false));

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
