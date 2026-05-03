import apiClient from "./client";

export interface AccessReviewScope {
  connector_ids: string[] | null;
  groups: string[] | null;
  user_emails: string[] | null;
}

export interface AccessReviewCreate {
  title: string;
  scope: AccessReviewScope;
}

export interface AccessReviewDecisionItem {
  decision: "keep" | "revoke";
  note: string;
}

export interface AccessReviewDecisionsSubmit {
  decisions: Record<string, AccessReviewDecisionItem>;
}

export interface AccessReviewOut {
  id: string;
  title: string;
  scope: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
  collected_at: string | null;
  approved_at: string | null;
  completed_at: string | null;
  snapshot: Record<string, unknown> | null;
  decisions: Record<string, AccessReviewDecisionItem> | null;
  created_by: string | null;
}

export interface ApproveOut {
  review_id: string;
  status: string;
  generated_change_requests: number;
}

const BASE = "/api/access-reviews";

export const accessReviewsApi = {
  list: (): Promise<AccessReviewOut[]> =>
    apiClient.get<AccessReviewOut[]>(BASE).then((r) => r.data),

  get: (id: string): Promise<AccessReviewOut> =>
    apiClient.get<AccessReviewOut>(`${BASE}/${id}`).then((r) => r.data),

  create: (body: AccessReviewCreate): Promise<AccessReviewOut> =>
    apiClient.post<AccessReviewOut>(BASE, body).then((r) => r.data),

  submitDecisions: (id: string, body: AccessReviewDecisionsSubmit): Promise<AccessReviewOut> =>
    apiClient.post<AccessReviewOut>(`${BASE}/${id}/decisions`, body).then((r) => r.data),

  approve: (id: string): Promise<ApproveOut> =>
    apiClient.post<ApproveOut>(`${BASE}/${id}/approve`).then((r) => r.data),

  getChanges: (id: string): Promise<{ change_request_id: string }[]> =>
    apiClient.get(`${BASE}/${id}/changes`).then((r) => r.data),
};
