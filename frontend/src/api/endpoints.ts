import { apiClient } from "./client";
import type {
  Token, User, Asset, AssetCreate, AssetUpdate, AssetListParams, BulkTagBody,
  Project, ProjectSummary, ProjectDetail, ProjectMember,
  ProjectCreate, ProjectUpdate, ProjectMemberCreate, ProjectMemberUpdate,
  OrgSettings, AIChatResponse, AIChatRequest, PromptPreviewResponse,
  Connector, ConnectorCreate, ConnectorTestResult, IngestResponse,
  ChangeRequest, ChangeRequestSummary, ChangeRequestCreate,
  ChangePlan, Approval, ApprovalCreate, ExecutionRun, AuditEvent,
} from "../types/api";

// Auth
export const authApi = {
  login: (email: string, password: string) =>
    apiClient.post<Token>("/auth/login", { email, password }).then((r) => r.data),
  logout: () => apiClient.post("/auth/logout"),
  me: () => apiClient.get<User>("/auth/me").then((r) => r.data),
};

// Assets
export const assetsApi = {
  list: (params?: AssetListParams) =>
    apiClient.get<Asset[]>("/assets", { params }).then((r) => r.data),
  get: (id: string) =>
    apiClient.get<Asset>(`/assets/${id}`).then((r) => r.data),
  create: (data: AssetCreate) =>
    apiClient.post<Asset>("/assets", data).then((r) => r.data),
  update: (id: string, data: AssetUpdate) =>
    apiClient.patch<Asset>(`/assets/${id}`, data).then((r) => r.data),
  tags: () =>
    apiClient.get<string[]>("/assets/tags").then((r) => r.data),
  bulkTag: (data: BulkTagBody) =>
    apiClient.patch<{ updated: number }>("/assets/bulk-tag", data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/assets/${id}`),
};

// Projects
export const projectsApi = {
  list: () =>
    apiClient.get<ProjectSummary[]>("/projects").then((r) => r.data),
  get: (id: string) =>
    apiClient.get<ProjectDetail>(`/projects/${id}`).then((r) => r.data),
  create: (data: ProjectCreate) =>
    apiClient.post<Project>("/projects", data).then((r) => r.data),
  update: (id: string, data: ProjectUpdate) =>
    apiClient.patch<Project>(`/projects/${id}`, data).then((r) => r.data),
  addMember: (id: string, data: ProjectMemberCreate) =>
    apiClient.post<ProjectMember>(`/projects/${id}/members`, data).then((r) => r.data),
  removeMember: (id: string, pcrId: string) =>
    apiClient.delete(`/projects/${id}/members/${pcrId}`),
  updateMember: (id: string, pcrId: string, data: ProjectMemberUpdate) =>
    apiClient.patch<ProjectMember>(`/projects/${id}/members/${pcrId}`, data).then((r) => r.data),
  aiChat: (id: string, data: AIChatRequest) =>
    apiClient.post<AIChatResponse>(`/projects/${id}/ai/chat`, data).then((r) => r.data),
  getPromptPreview: (id: string, draftMessage?: string) =>
    apiClient
      .get<PromptPreviewResponse>(`/projects/${id}/ai/prompt-preview`, {
        params: draftMessage ? { draft_message: draftMessage } : {},
      })
      .then((r) => r.data),
};

// Settings
export const settingsApi = {
  get: () =>
    apiClient.get<OrgSettings>("/settings").then((r) => r.data),
  updateAIKey: (api_key: string) =>
    apiClient.put<OrgSettings>("/settings/ai-key", { api_key }).then((r) => r.data),
  generateAgentSecret: () =>
    apiClient.post<OrgSettings>("/settings/agent-secret").then((r) => r.data),
};

// Connectors
export const connectorsApi = {
  list: () => apiClient.get<Connector[]>("/connectors").then((r) => r.data),
  create: (data: ConnectorCreate) => apiClient.post<Connector>("/connectors", data).then((r) => r.data),
  delete: (id: string) => apiClient.delete(`/connectors/${id}`),
  test: (id: string) =>
    apiClient.post<ConnectorTestResult>(`/connectors/${id}/test`).then((r) => r.data),
  ingest: (id: string, actionId: string) =>
    apiClient.post<IngestResponse>(`/connectors/${id}/ingest/${actionId}`).then((r) => r.data),
};

// Change Requests
export const changeRequestsApi = {
  list: (params?: { status?: string; risk_level?: string; change_type?: string; asset_id?: string }) =>
    apiClient.get<ChangeRequestSummary[]>("/change-requests", { params }).then((r) => r.data),
  get: (id: string) =>
    apiClient.get<ChangeRequest>(`/change-requests/${id}`).then((r) => r.data),
  create: (data: ChangeRequestCreate) =>
    apiClient.post<ChangeRequest>("/change-requests", data).then((r) => r.data),
  generatePlan: (id: string) =>
    apiClient.post<ChangePlan>(`/change-requests/${id}/plan`).then((r) => r.data),
  submitForApproval: (id: string) =>
    apiClient.post<ChangeRequest>(`/change-requests/${id}/submit-for-approval`).then((r) => r.data),
  approve: (id: string, data: ApprovalCreate) =>
    apiClient.post<Approval>(`/change-requests/${id}/approve`, data).then((r) => r.data),
  reject: (id: string, data: ApprovalCreate) =>
    apiClient.post<Approval>(`/change-requests/${id}/reject`, data).then((r) => r.data),
  execute: (id: string) =>
    apiClient.post<ExecutionRun>(`/change-requests/${id}/execute`).then((r) => r.data),
  rollback: (id: string) =>
    apiClient.post<ExecutionRun>(`/change-requests/${id}/rollback`).then((r) => r.data),
  cancel: (id: string) =>
    apiClient.post<ChangeRequest>(`/change-requests/${id}/cancel`).then((r) => r.data),
  getAuditEvents: (id: string) =>
    apiClient.get<AuditEvent[]>(`/change-requests/${id}/audit-events`).then((r) => r.data),
  getProgress: (id: string) =>
    apiClient.get(`/change-requests/${id}/progress`).then((r) => r.data),
};

// Audit
export const auditApi = {
  list: (params?: { limit?: number; offset?: number; event_type?: string }) =>
    apiClient.get<AuditEvent[]>("/audit-events", { params }).then((r) => r.data),
};

// ---- Compliance ----

export interface CisFailingAsset {
  id: string;
  name: string;
  detail: string;
}

export interface CisCheckRow {
  id: string;
  title: string;
  pass_count: number;
  fail_count: number;
  failing_assets: CisFailingAsset[];
}

export interface CisControlRow {
  id: number;
  name: string;
  method: "asset_coverage" | "agent_audit" | "not_tracked";
  score: number | null;
  assets_passing: number | null;
  assets_total: number | null;
  checks: CisCheckRow[];
}

export interface CisSummaryResponse {
  overall_score: number | null;
  tracked_controls: number;
  last_updated: string | null;
  controls: CisControlRow[];
}

export const complianceApi = {
  getSummary: (): Promise<CisSummaryResponse> =>
    apiClient.get<CisSummaryResponse>("/compliance/cis-summary").then((r) => r.data),
};

// Recurring Jobs
export interface RecurringJob {
  id: string;
  organization_id: string;
  name: string;
  job_type: "backup" | "scheduled_restore" | "scheduled_op";
  connector_id: string | null;
  action_id: string;
  parameters: Record<string, unknown>;
  target_description: string;
  cron_expression: string;
  schedule_preset: string | null;
  schedule_hour: number | null;
  enabled: boolean;
  last_run_at: string | null;
  last_cr_id: string | null;
  next_run_at: string | null;
  created_by: string;
  created_at: string;
}

export interface RecurringJobCreate {
  name: string;
  job_type: "backup" | "scheduled_restore" | "scheduled_op";
  connector_id?: string;
  action_id: string;
  parameters?: Record<string, unknown>;
  target_description: string;
  cron_expression: string;
  schedule_preset?: string;
  schedule_hour?: number;
}

export const recurringJobsApi = {
  list: (job_type?: string) =>
    apiClient.get<RecurringJob[]>("/recurring-jobs", { params: job_type ? { job_type } : {} }).then((r) => r.data),
  get: (id: string) =>
    apiClient.get<RecurringJob>(`/recurring-jobs/${id}`).then((r) => r.data),
  create: (data: RecurringJobCreate) =>
    apiClient.post<RecurringJob>("/recurring-jobs", data).then((r) => r.data),
  update: (id: string, data: Partial<RecurringJobCreate> & { enabled?: boolean }) =>
    apiClient.put<RecurringJob>(`/recurring-jobs/${id}`, data).then((r) => r.data),
  delete: (id: string) =>
    apiClient.delete(`/recurring-jobs/${id}`),
  enable: (id: string) =>
    apiClient.post<RecurringJob>(`/recurring-jobs/${id}/enable`).then((r) => r.data),
  disable: (id: string) =>
    apiClient.post<RecurringJob>(`/recurring-jobs/${id}/disable`).then((r) => r.data),
  runNow: (id: string) =>
    apiClient.post<RecurringJob>(`/recurring-jobs/${id}/run-now`).then((r) => r.data),
};

// ─── Backup & Recovery ───────────────────────────────────────────────────────

export interface BackupTarget {
  id: string;
  organization_id: string;
  recurring_job_id: string | null;
  asset_id: string | null;
  target_description: string;
  expected_cadence_hours: number;
  last_successful_backup_cr_id: string | null;
  last_successful_at: string | null;
  status: "healthy" | "overdue" | "unprotected";
  created_at: string;
}

export interface BackupHistoryItem {
  id: string;
  title: string;
  change_type: string;
  status: string;
  artifact_refs: Record<string, unknown> | null;
  created_at: string;
}

export interface BackupContext {
  has_backup: boolean;
  last_successful_at: string | null;
  artifact: Record<string, unknown> | null;
  backup_cr_id: string | null;
  overdue: boolean;
}

export interface RestoreCrCreate {
  source_cr_id: string;
  target_description: string;
  restore_type?: string;
  notes?: string;
}

export const backupApi = {
  listTargets: () =>
    apiClient.get<BackupTarget[]>("/backup-targets").then((r) => r.data),
  getTarget: (id: string) =>
    apiClient.get<BackupTarget>(`/backup-targets/${id}`).then((r) => r.data),
  createTarget: (data: { target_description: string; expected_cadence_hours?: number; recurring_job_id?: string; asset_id?: string }) =>
    apiClient.post<BackupTarget>("/backup-targets", data).then((r) => r.data),
  listHistory: (limit = 50, offset = 0) =>
    apiClient.get<BackupHistoryItem[]>("/backup-history", { params: { limit, offset } }).then((r) => r.data),
  createRestoreCr: (data: RestoreCrCreate) =>
    apiClient.post<{ id: string; status: string; title: string }>("/restore-crs", data).then((r) => r.data),
  getBackupContext: (crId: string) =>
    apiClient.get<BackupContext>(`/change-requests/${crId}/backup-context`).then((r) => r.data),
};
