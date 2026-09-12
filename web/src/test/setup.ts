import "@testing-library/jest-dom/vitest";

// L6 Task 6.1 fills in the MSW request handlers and imports the `server` instance here.
// Phase 0 ships the jest-dom import and the lifecycle wiring commented out so the shape
// is obvious to the lane that lands the first component test:
//
// import { server } from "./msw-server";
// beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
// afterEach(() => server.resetHandlers());
// afterAll(() => server.close());
