import { apiClient } from "./client";
import type {
  Token, User, Asset, AssetCreate, AssetUpdate, AssetListParams, BulkTagBody,
  Connector, ConnectorCreate, ConnectorTestResult,
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
};

// Connectors
export const connectorsApi = {
  list: () => apiClient.get<Connector[]>("/connectors").then((r) => r.data),
  create: (data: ConnectorCreate) => apiClient.post<Connector>("/connectors", data).then((r) => r.data),
  test: (id: string) =>
    apiClient.post<ConnectorTestResult>(`/connectors/${id}/test`).then((r) => r.data),
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
  getAuditEvents: (id: string) =>
    apiClient.get<AuditEvent[]>(`/change-requests/${id}/audit-events`).then((r) => r.data),
};

// Audit
export const auditApi = {
  list: (params?: { limit?: number; offset?: number; event_type?: string }) =>
    apiClient.get<AuditEvent[]>("/audit-events", { params }).then((r) => r.data),
};
