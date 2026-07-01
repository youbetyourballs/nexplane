// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { apiClient } from "./client";

export interface SmokeTestLastRun {
  run_id: string;
  status: "passed" | "failed" | "unknown";
  phases_passed: number;
  phases_failed: number;
  phases_passed_list: string[];
  phases_failed_list: string[];
  exit_code: number;
  finished_at: string;
}

export interface SmokeTestSuite {
  id: string;
  name: string;
  file: string;
  default_phases: string;
  slow_phases: string;
  description: string;
  last_run: SmokeTestLastRun | null;
  running_run_id: string | null;
}

export interface RunResult {
  run_id: string;
  pid: number;
  log_path: string;
}

export interface LogChunk {
  content: string;
  next_offset: number;
  done: boolean;
}

export interface RunConfig {
  suite: string;
  phases?: string;
  tailscale_auth_key?: string;
  gcp_project?: string;
  azure_resource_group?: string;
  email?: string;
  password?: string;
}

export interface CleanupAsset {
  id: string;
  name: string;
  asset_type: string;
  created_at: string | null;
}

export interface CleanupPreview {
  assets: CleanupAsset[];
  count: number;
}

export interface SmokeRun {
  id: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  phases: string[];
  started_at: string;
  completed_at: string | null;
  runner_instance_id: string | null;
  result_summary: Record<string, PhaseResult> | null;
  error: string | null;
}

export interface PhaseResult {
  phase: string;
  status: 'passed' | 'failed' | 'skipped';
  duration_seconds: number;
  connectors_exercised: string[];
  connectors_skipped: string[];
  rollback_verified: boolean;
  coverage_gaps: string[];
}

export const startSmokeRun = async (phases: string[]): Promise<SmokeRun> => {
  const res = await apiClient.post('/smoke-tests/runs', { phases });
  return res.data;
};

export const listSmokeRuns = async (): Promise<SmokeRun[]> => {
  const res = await apiClient.get('/smoke-tests/runs');
  return res.data;
};

export const getSmokeRun = async (id: string): Promise<SmokeRun> => {
  const res = await apiClient.get(`/smoke-tests/runs/${id}`);
  return res.data;
};

export const cancelSmokeRun = async (id: string): Promise<void> => {
  await apiClient.delete(`/smoke-tests/runs/${id}`);
};

export const smokeTestsApi = {
  getSuites: () =>
    apiClient.get<SmokeTestSuite[]>("/smoke-tests/suites").then((r) => r.data),

  triggerRun: (config: RunConfig) =>
    apiClient.post<RunResult>("/smoke-tests/run", config).then((r) => r.data),

  getLogs: (runId: string, offset: number = 0) =>
    apiClient
      .get<LogChunk>(`/smoke-tests/logs/${runId}`, { params: { offset } })
      .then((r) => r.data),

  stopRun: (runId: string) =>
    apiClient.delete(`/smoke-tests/runs/${runId}`).then((r) => r.data),

  getCleanupPreview: (): Promise<CleanupPreview> =>
    apiClient.get<CleanupPreview>("/smoke-tests/cleanup-preview").then((r) => r.data),

  executeCleanup: (): Promise<{ deleted: number }> =>
    apiClient.delete<{ deleted: number }>("/smoke-tests/cleanup-inventory").then((r) => r.data),
};
