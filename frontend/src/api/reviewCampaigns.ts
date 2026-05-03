import { apiClient } from "./client";

export interface CampaignScope {
  connector_ids?: string[] | null;
  asset_tags?: string[] | null;
  user_groups?: string[] | null;
  include_inactive_users?: boolean;
}

export interface ReviewerAssignmentRule {
  type: "manager_centric" | "resource_owner" | "security_team";
  fallback_reviewer_id?: string | null;
}

export interface EvidenceOptions {
  include_last_login: boolean;
  include_days_inactive: boolean;
  include_asset_sensitivity: boolean;
}

export interface CampaignCreate {
  title: string;
  description?: string;
  campaign_type: "manager_centric" | "resource_owner" | "security_team";
  scope: CampaignScope;
  reviewer_assignment_rule: ReviewerAssignmentRule;
  evidence_options: EvidenceOptions;
  due_date?: string | null;
}

export interface CampaignOut {
  id: string;
  organization_id: string;
  created_by: string;
  title: string;
  description: string | null;
  campaign_type: string;
  scope: CampaignScope;
  reviewer_assignment_rule: ReviewerAssignmentRule;
  evidence_options: EvidenceOptions;
  status: string;
  due_date: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
}

export interface ReviewEntryOut {
  id: string;
  campaign_id: string;
  user_email: string;
  user_display_name: string | null;
  user_status: string;
  resource_name: string;
  resource_type: string;
  connector_id: string | null;
  permission_level: string;
  is_privileged: boolean;
  evidence: {
    last_login_at?: string | null;
    days_inactive?: number | null;
    flagged_inactive?: boolean;
    asset_criticality?: string | null;
    asset_tags?: string[];
    flagged_privileged?: boolean;
  };
  reviewer_id: string | null;
  reviewer_unresolved: boolean;
  decision: "keep" | "revoke" | null;
  decision_note: string | null;
  decided_at: string | null;
  decided_by: string | null;
  change_request_id: string | null;
}

export interface CampaignApproveOut {
  campaign_id: string;
  status: string;
  revocations_created: number;
}

const BASE = "/review-campaigns";

export const reviewCampaignsApi = {
  list: (params?: { mine?: boolean; status?: string }): Promise<CampaignOut[]> =>
    apiClient.get<CampaignOut[]>(BASE, { params }).then((r) => r.data),

  get: (id: string): Promise<CampaignOut> =>
    apiClient.get<CampaignOut>(`${BASE}/${id}`).then((r) => r.data),

  create: (body: CampaignCreate): Promise<CampaignOut> =>
    apiClient.post<CampaignOut>(BASE, body).then((r) => r.data),

  launch: (id: string): Promise<CampaignOut> =>
    apiClient.post<CampaignOut>(`${BASE}/${id}/launch`).then((r) => r.data),

  cancel: (id: string): Promise<CampaignOut> =>
    apiClient.post<CampaignOut>(`${BASE}/${id}/cancel`).then((r) => r.data),

  listEntries: (id: string, params?: { reviewer_id?: string; decision?: string; flagged?: boolean }): Promise<ReviewEntryOut[]> =>
    apiClient.get<ReviewEntryOut[]>(`${BASE}/${id}/entries`, { params }).then((r) => r.data),

  submitDecision: (campaignId: string, entryId: string, body: { decision: "keep" | "revoke"; note?: string }): Promise<ReviewEntryOut> =>
    apiClient.put<ReviewEntryOut>(`${BASE}/${campaignId}/entries/${entryId}`, body).then((r) => r.data),

  approve: (id: string): Promise<CampaignApproveOut> =>
    apiClient.post<CampaignApproveOut>(`${BASE}/${id}/approve`).then((r) => r.data),

  exportEvidence: (id: string): Promise<unknown> =>
    apiClient.get(`${BASE}/${id}/evidence-export`).then((r) => r.data),
};
