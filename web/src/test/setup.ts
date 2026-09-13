import "@testing-library/jest-dom/vitest";
import { afterAll, afterEach, beforeAll } from "vitest";

import { server } from "@/mocks/server";

// Every request a test makes is served by the MSW handlers in `src/mocks/handlers.ts`.
// `onUnhandledRequest: "error"` is deliberate: a component that reaches a route nobody
// mocked should fail the test rather than quietly resolve against the network.
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
