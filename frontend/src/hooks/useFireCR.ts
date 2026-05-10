import { useState } from "react";
import { changeRequestsApi } from "../api/endpoints";
import type { ChangeType } from "../types/api";

interface FireCRParams {
  title: string;
  changeType: string;
  targetAssetId: string;
  parameters: Record<string, unknown>;
}

export function useFireCR() {
  const [loading, setLoading] = useState(false);

  async function fireCR(params: FireCRParams): Promise<Record<string, unknown>> {
    setLoading(true);
    try {
      const cr = await changeRequestsApi.create({
        title: params.title,
        change_type: params.changeType as ChangeType,
        target_asset_ids: [params.targetAssetId],
        desired_outcome: params.parameters,
      });
      // Auto-execute: full workflow (plan → submit → approve → execute)
      await changeRequestsApi.generatePlan(cr.id);
      await changeRequestsApi.submitForApproval(cr.id);
      await changeRequestsApi.approve(cr.id, { decision: "approved", comment: "Auto-approved by wizard" });
      const run = await changeRequestsApi.execute(cr.id);
      return (run as unknown as Record<string, unknown>) ?? {};
    } finally {
      setLoading(false);
    }
  }

  return { fireCR, loading };
}
