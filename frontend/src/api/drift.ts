// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { apiClient } from "./client";

export interface DriftEventRead {
  id: string;
  organization_id: string;
  asset_id: string;
  surface_type: string;
  drift_policy_id: string;
  baseline_state: Record<string, unknown>;
  observed_state: Record<string, unknown>;
  diff: { added: Record<string, unknown>; removed: Record<string, unknown>; changed: Record<string, { from: unknown; to: unknown }> };
  severity: "high" | "medium" | "low";
  detected_at: string;
  status: "open" | "accepted" | "attested" | "dismissed";
  shadow_cr_id: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_note: string | null;
  attested_suppress_until: string | null;
}

export interface DriftPolicyRead {
  id: string;
  organization_id: string;
  name: string;
  scope_type: string;
  scope_value: string;
  surface_types: string[];
  poll_interval_seconds: number;
  auto_created: boolean;
  enabled: boolean;
  source_cr_id: string | null;
  created_at: string;
  last_checked_at: string | null;
}

export async function listDriftEvents(params?: {
  status?: string;
  asset_id?: string;
  surface_type?: string;
  severity?: string;
}): Promise<DriftEventRead[]> {
  const query = new URLSearchParams(
    Object.fromEntries(Object.entries(params || {}).filter(([, v]) => v !== undefined)) as Record<string, string>
  );
  return apiClient.get(`/drift/events?${query}`).then((r) => r.data);
}

export async function getDriftEvent(id: string): Promise<DriftEventRead> {
  return apiClient.get(`/drift/events/${id}`).then((r) => r.data);
}

export async function acceptDriftEvent(id: string, note: string): Promise<DriftEventRead> {
  return apiClient.post(`/drift/events/${id}/accept`, { note }).then((r) => r.data);
}

export async function attestDriftEvent(id: string, note: string, snooze_days: number): Promise<DriftEventRead> {
  return apiClient.post(`/drift/events/${id}/attest`, { note, snooze_days }).then((r) => r.data);
}

export async function dismissDriftEvent(id: string): Promise<DriftEventRead> {
  return apiClient.post(`/drift/events/${id}/dismiss`).then((r) => r.data);
}

export async function getAssetDrift(assetId: string): Promise<{
  asset_id: string;
  resource_states: Array<{ surface_type: string; state: Record<string, unknown>; captured_at: string; source: string }>;
  open_events: DriftEventRead[];
}> {
  return apiClient.get(`/assets/${assetId}/drift`).then((r) => r.data);
}
