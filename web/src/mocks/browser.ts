import { setupWorker } from "msw/browser";

import { handlers } from "./handlers";

/** Browser-side MSW instance, started by `<Providers>` when `NEXT_PUBLIC_API_MOCK=1`. */
export const worker = setupWorker(...handlers);
