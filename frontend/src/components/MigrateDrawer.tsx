// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { apiClient } from "../api/client";

interface Props {
  assetId: string;
  assetName: string;
  onClose: () => void;
}

interface KubeCluster {
  id: string;
  name: string;
}

export function MigrateDrawer({ assetId, assetName, onClose }: Props) {
  const navigate = useNavigate();
  const [registry, setRegistry] = useState("");
  const [clusterId, setClusterId] = useState("");
  const [namespace, setNamespace] = useState("nexplane-migrations");
  const [soakSeconds, setSoakSeconds] = useState(120);
  const [dryRun, setDryRun] = useState(false);

  const { data: clusters = [] } = useQuery<KubeCluster[]>({
    queryKey: ["kube-clusters"],
    queryFn: () =>
      apiClient
        .get<KubeCluster[]>("/assets", { params: { asset_type: "kubernetes_cluster" } })
        .then((r) => r.data),
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      const cr = await apiClient
        .post("/change-requests", {
          title: `Migrate ${assetName} to Kubernetes`,
          description: `AI-directed autonomous migration of workloads on ${assetName}.`,
          change_type: "agent_containerize_auto",
          target_asset_ids: [assetId],
          desired_outcome: {
            registry,
            target_cluster_id: clusterId,
            namespace,
            soak_seconds: soakSeconds,
            dry_run: dryRun,
          },
          risk_level: "high",
        })
        .then((r) => r.data);
      await apiClient.post(`/change-requests/${cr.id}/plan`);
      await apiClient.post(`/change-requests/${cr.id}/submit-for-approval`);
      await apiClient.post(`/change-requests/${cr.id}/approve`, {
        decision: "approved",
        comment: "Auto-approved via Migrate drawer",
      });
      await apiClient.post(`/change-requests/${cr.id}/execute`);
      return cr;
    },
    onSuccess: (cr) => {
      navigate(`/change-requests/${cr.id}`);
    },
  });

  const canSubmit = registry.trim() !== "" && clusterId !== "" && !runMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div
        className="w-full max-w-md bg-white h-full shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <div>
            <h2 className="font-semibold text-slate-900">Migrate to Kubernetes ✨</h2>
            <p className="text-xs text-slate-500 mt-0.5">{assetName}</p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xl">✕</button>
        </div>

        <div className="px-6 py-5 space-y-5">
          <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs text-blue-700">
            AI will discover all workloads, analyse dependencies, generate Dockerfiles, deploy to Kubernetes, and verify the migration — all autonomously.
          </div>

          {/* Registry */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Container Registry URL <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={registry}
              onChange={(e) => setRegistry(e.target.value)}
              placeholder="123456.dkr.ecr.us-east-1.amazonaws.com"
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Target cluster */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Target Kubernetes Cluster <span className="text-red-500">*</span>
            </label>
            {clusters.length === 0 ? (
              <p className="text-xs text-amber-600">
                No Kubernetes clusters found in inventory. Add a cluster asset first.
              </p>
            ) : (
              <select
                value={clusterId}
                onChange={(e) => setClusterId(e.target.value)}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value="">Select a cluster…</option>
                {clusters.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            )}
          </div>

          {/* Namespace */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">Namespace</label>
            <input
              type="text"
              value={namespace}
              onChange={(e) => setNamespace(e.target.value)}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {/* Soak duration */}
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1.5">
              Soak duration: <span className="font-mono">{soakSeconds}s</span>
            </label>
            <input
              type="range"
              min={10}
              max={600}
              step={10}
              value={soakSeconds}
              onChange={(e) => setSoakSeconds(Number(e.target.value))}
              className="w-full"
            />
            <div className="flex justify-between text-xs text-slate-400 mt-0.5">
              <span>10s</span><span>600s</span>
            </div>
          </div>

          {/* Dry run */}
          <label className="flex items-center gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={dryRun}
              onChange={(e) => setDryRun(e.target.checked)}
              className="w-4 h-4"
            />
            <div>
              <p className="text-sm font-medium text-slate-700">Dry run</p>
              <p className="text-xs text-slate-400">Runs discovery and AI analysis only — no build or deploy</p>
            </div>
          </label>

          {/* Submit */}
          <button
            onClick={() => runMutation.mutate()}
            disabled={!canSubmit}
            className="w-full py-2.5 bg-purple-600 text-white rounded font-medium text-sm hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {runMutation.isPending
              ? "Launching migration…"
              : dryRun
              ? "Run Discovery & Analysis →"
              : "Launch Migration →"}
          </button>

          {runMutation.isError && (
            <p className="text-sm text-red-600">Failed to launch migration. Please try again.</p>
          )}
        </div>
      </div>
    </div>
  );
}
