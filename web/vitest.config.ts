import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
    // The fetchers build absolute URLs from this base, so `fetch` resolves under Node.
    env: { NEXT_PUBLIC_API_BASE: "http://localhost:3000", NEXT_PUBLIC_API_MOCK: "1" },
  },
});
