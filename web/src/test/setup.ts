import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll, beforeEach } from "vitest";

import { server } from "@/mocks/server";

/**
 * jsdom ships no `matchMedia`, and Motion's `useReducedMotion` needs one. Motion queries it once
 * and then relies on a change listener, so the stub keeps a live registry and
 * `setPrefersReducedMotion` notifies it — otherwise the first test in a file would decide the
 * value for every test after it.
 */
let prefersReducedMotion = false;
const listeners = new Set<(event: MediaQueryListEvent) => void>();

export function setPrefersReducedMotion(value: boolean): void {
  if (value === prefersReducedMotion) return;
  prefersReducedMotion = value;
  const event = { matches: value, media: "(prefers-reduced-motion: reduce)" } as MediaQueryListEvent;
  for (const listener of listeners) listener(event);
}

window.matchMedia = ((query: string) => {
  const reducedMotionQuery = query.includes("prefers-reduced-motion");
  const list = {
    get matches() {
      return reducedMotionQuery ? prefersReducedMotion : false;
    },
    media: query,
    onchange: null,
    addListener: (listener: (event: MediaQueryListEvent) => void) => {
      if (reducedMotionQuery) listeners.add(listener);
    },
    removeListener: (listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
      if (reducedMotionQuery) listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) =>
      listeners.delete(listener),
    dispatchEvent: () => false,
  };
  return list as unknown as MediaQueryList;
}) as typeof window.matchMedia;

beforeEach(() => setPrefersReducedMotion(false));

// Every request a test makes is served by the MSW handlers in `src/mocks/handlers.ts`.
// `onUnhandledRequest: "error"` is deliberate: a component that reaches a route nobody mocked
// should fail the test rather than quietly resolve against the network.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
