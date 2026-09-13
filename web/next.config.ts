import createMDX from "@next/mdx";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  pageExtensions: ["ts", "tsx", "mdx"],
  env: {
    // Lane L6 builds entirely against MSW: there is no API server to talk to. The default is
    // therefore "on", and lane L9 turns it off (`NEXT_PUBLIC_API_MOCK=0`) when it wires the
    // real API and SSE. Setting it here keeps the value in a committed file rather than in a
    // `.env` that `web/.gitignore` excludes.
    NEXT_PUBLIC_API_MOCK: process.env.NEXT_PUBLIC_API_MOCK ?? "1",
  },
};

export default createMDX()(nextConfig);
