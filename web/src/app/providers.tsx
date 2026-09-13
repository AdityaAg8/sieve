"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

/**
 * True once the app may issue requests.
 *
 * With `NEXT_PUBLIC_API_MOCK=1` the browser's MSW service worker takes a moment to register,
 * and a query that fires before it is ready escapes to the real network. Rather than block the
 * whole tree behind that promise — which would delay first paint and the LCP element — the shell
 * renders immediately and the data hooks stay disabled until the worker is listening. Skeletons
 * cover the gap, which is what they are for.
 */
const ApiReadyContext = createContext(true);

export function useApiReady(): boolean {
  return useContext(ApiReadyContext);
}

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

function getQueryClient(): QueryClient {
  if (typeof window === "undefined") return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}

/**
 * Written as a literal comparison against the inlined build-time value, not as a call to
 * `mocksEnabled()`, and that is the whole point: the bundler folds `"0" === "1"` to `false` and
 * deletes the branch, `import("@/mocks/browser")` included. Behind a function call it could not,
 * and every production bundle would carry `msw` — a dev dependency — in a chunk nobody loads.
 * `scripts/check-no-msw.mjs` fails the build if that ever stops being true.
 */
const MOCKS_ENABLED = process.env.NEXT_PUBLIC_API_MOCK === "1";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(getQueryClient);
  const [ready, setReady] = useState(!MOCKS_ENABLED);

  useEffect(() => {
    if (!MOCKS_ENABLED) return;
    let cancelled = false;
    void import("@/mocks/browser")
      .then(({ worker }) =>
        worker.start({
          // Next's own assets, fonts and RSC payloads must reach the network untouched.
          onUnhandledRequest: "bypass",
          quiet: true,
          serviceWorker: { url: "/mockServiceWorker.js" },
        }),
      )
      .then(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <ApiReadyContext value={ready}>{children}</ApiReadyContext>
    </QueryClientProvider>
  );
}
