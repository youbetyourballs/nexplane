import apiClient from "./client"; // existing axios instance

export interface IRPlaybookTemplate {
  id: string;
  playbook_type: string;
  display_name: string;
  description: string | null;
  default_parameters: Record<string, unknown>;
  ir_auto_approve: boolean;
}

export interface StepResult {
  status: "completed" | "failed" | "skipped";
  started_at: string;
  completed_at: string;
  error?: string;
  state?: Record<string, unknown>;
}

export interface ForensicBundle {
  id: string;
  asset_id: string;
  change_request_id: string | null;
  manifest: Record<string, unknown>;
  size_bytes: number | null;
  collected_at: string;
}

export interface InstantiatePayload {
  playbook_type: string;
  parameters: Record<string, unknown>;
}

export const irApi = {
  getTemplates: () =>
    apiClient.get<IRPlaybookTemplate[]>("/api/ir/templates").then((r) => r.data),

  instantiate: (payload: InstantiatePayload) =>
    apiClient.post<Record<string, unknown>>("/api/ir/instantiate", payload).then((r) => r.data),

  getBundles: (params?: { asset_id?: string; since?: string; until?: string; limit?: number }) =>
    apiClient.get<ForensicBundle[]>("/api/ir/bundles", { params }).then((r) => r.data),

  getBundle: (bundleId: string) =>
    apiClient.get<ForensicBundle>(`/api/ir/bundles/${bundleId}`).then((r) => r.data),

  getBundleDownloadUrl: (bundleId: string) =>
    apiClient.get<{ url: string }>(`/api/ir/bundles/${bundleId}/download-url`).then((r) => r.data),
};
