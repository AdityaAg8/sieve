import type { Metadata } from "next";

import { FindingPage } from "@/components/findings/FindingPage";

export const metadata: Metadata = {
  title: "Finding — Sieve",
  description: "One tool definition, the verdict, and the span Sieve read.",
};

export default async function DeepLinkedFindingPage(
  props: PageProps<"/scan/[id]/finding/[fid]">,
) {
  const { id, fid } = await props.params;
  return <FindingPage scanId={id} findingId={decodeURIComponent(fid)} />;
}
