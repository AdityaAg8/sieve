"use client";

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { useApiReady } from "@/app/providers";
import {
  getBench,
  getFindings,
  getModels,
  getScan,
  type BenchSummary,
  type FindingPage,
  type FindingQuery,
  type ModelInfo,
  type ScanSummary,
} from "./api";

export const queryKeys = {
  scan: (id: string) => ["scan", id] as const,
  findings: (id: string, filters?: FindingQuery) => ["findings", id, filters ?? {}] as const,
  bench: () => ["bench"] as const,
  models: () => ["models"] as const,
};

export function useScan(id: string): UseQueryResult<ScanSummary> {
  const ready = useApiReady();
  return useQuery({
    queryKey: queryKeys.scan(id),
    queryFn: () => getScan(id),
    enabled: ready && id.length > 0,
  });
}

export function useFindings(
  id: string,
  filters?: FindingQuery,
  options?: { enabled?: boolean },
): UseQueryResult<FindingPage> {
  const ready = useApiReady();
  return useQuery({
    queryKey: queryKeys.findings(id, filters),
    queryFn: () => getFindings(id, filters),
    enabled: ready && id.length > 0 && (options?.enabled ?? true),
  });
}

export function useBench(): UseQueryResult<BenchSummary> {
  const ready = useApiReady();
  return useQuery({ queryKey: queryKeys.bench(), queryFn: getBench, enabled: ready });
}

export function useModels(): UseQueryResult<ModelInfo> {
  const ready = useApiReady();
  return useQuery({ queryKey: queryKeys.models(), queryFn: getModels, enabled: ready });
}
