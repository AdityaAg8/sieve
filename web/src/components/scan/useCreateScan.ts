"use client";

import { useMutation } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { ApiError, createScan, type ScanCreate } from "@/lib/api";

/**
 * Starts a scan and goes to its results page. A failure is reported in the interface's own
 * voice — what happened and what to do — never as a raw status code.
 */
export function useCreateScan() {
  const router = useRouter();

  const mutation = useMutation({
    mutationFn: (body: ScanCreate) => createScan(body),
    onSuccess: (summary) => router.push(`/scan/${summary.id}`),
  });

  const message = mutation.error
    ? mutation.error instanceof ApiError && mutation.error.status === 429
      ? "You have reached the scan limit for now. Wait a minute and try again."
      : "The scan could not be started. Check your connection and try again."
    : null;

  return { start: mutation.mutate, pending: mutation.isPending, message };
}
