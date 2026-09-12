import { setupServer } from "msw/node";

import { handlers } from "./handlers";

/** Node-side MSW instance. Vitest starts and stops it in `src/test/setup.ts`. */
export const server = setupServer(...handlers);
