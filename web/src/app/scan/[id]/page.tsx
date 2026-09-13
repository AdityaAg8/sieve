import type { Metadata } from "next";

import { ResultsScreen } from "@/components/findings/ResultsScreen";

export const metadata: Metadata = {
  title: "Scan results — Sieve",
  description: "Confirmed findings first, with the scanner's verdict beside Sieve's.",
};

export default async function ScanResultsPage(props: PageProps<"/scan/[id]">) {
  const { id } = await props.params;
  return <ResultsScreen scanId={id} />;
}
