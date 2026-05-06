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
};
