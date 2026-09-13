import type { Metadata } from "next";

import { BenchmarkScreen } from "@/components/bench/BenchmarkScreen";

export const metadata: Metadata = {
  title: "Benchmark — Sieve",
  description:
    "SieveBench in full: headline numbers with cluster-bootstrap intervals, every ablation, agreement three ways, and the holdout log.",
};

export default function BenchmarkPage() {
  return <BenchmarkScreen />;
}
