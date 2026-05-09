import { useState } from "react";
import { Loader2, CheckCircle, Server, AlertCircle } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useFireCR } from "../hooks/useFireCR";
import { apiClient } from "../api/client";

interface KubernetesCluster { id: string; name: string; }

interface ContainerizeWizardStep3Props {
  assetId: string;
  apps: Array<{ name: string; data_directories: string[] }>;
  buildResults: Record<string, { imageDigest: string; manifests: Record<string, string> }>;
  onComplete: (deployResults: Record<string, { workloadAssetId: string }>) => void;
}

export function ContainerizeWizardStep3({ assetId, apps, buildResults, onComplete }: ContainerizeWizardStep3Props) {
  const [selectedCluster, setSelectedCluster] = useState("");
  const [namespace, setNamespace] = useState("default");
  const [status, setStatus] = useState<"idle" | "deploying" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  const { data: clusters = [] } = useQuery<KubernetesCluster[]>({
    queryKey: ["assets", "kubernetes_cluster"],
    queryFn: async () => {
      const res = await apiClient.get<KubernetesCluster[]>("/assets", { params: { asset_type: "kubernetes_cluster" } });
      return res.data ?? [];
    },
  });

  const { fireCR } = useFireCR();

  const handleDeploy = async () => {
    if (!selectedCluster) { setError("Select a Kubernetes cluster before deploying."); return; }
    setStatus("deploying"); setError(null);
    const results: Record<string, { workloadAssetId: string }> = {};
    for (const app of apps) {
      try {
        const crResult = await fireCR({
          title: `[Containerize] Deploy ${app.name} to k8s`,
          changeType: "k8s_workload_deploy",
          targetAssetId: assetId,
          parameters: { app_name: app.name, target_cluster_id: selectedCluster, namespace, dry_run: false },
        });
        results[app.name] = { workloadAssetId: (crResult?.workload_asset_id as string) ?? "" };
      } catch (e) {
        setError(`Deploy failed for ${app.name}: ${e instanceof Error ? e.message : String(e)}`);
        setStatus("error"); return;
      }
    }
    setStatus("done"); onComplete(results);
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-slate-900">Step 3 — Deploy to Kubernetes</h3>
        <p className="text-sm text-slate-500 mt-1">Select the target cluster and apply manifests.</p>
      </div>

      <div className="border border-slate-200 rounded-lg p-4 space-y-4">
        <p className="text-sm font-semibold text-slate-700">Target Cluster</p>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-slate-700">Kubernetes Cluster</label>
          {clusters.length === 0 ? (
            <p className="text-sm text-amber-600">No Kubernetes clusters registered. Add a cluster connector first.</p>
          ) : (
            <select
              className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
              value={selectedCluster}
              onChange={(e) => setSelectedCluster(e.target.value)}
              disabled={status === "deploying"}
            >
              <option value="">Select a cluster&hellip;</option>
              {clusters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="space-y-1">
          <label className="block text-sm font-medium text-slate-700">Namespace</label>
          <input
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            placeholder="default"
            disabled={status === "deploying"}
          />
        </div>
      </div>

      <div className="space-y-2">
        {apps.map((app) => (
          <div key={app.name} className="flex items-center justify-between p-3 border border-slate-200 rounded text-sm">
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-slate-400" />
              <span className="font-mono text-slate-800">{app.name}</span>
            </div>
            <div className="flex items-center gap-2 text-slate-400">
              {buildResults[app.name]?.imageDigest && (
                <span className="text-xs">{buildResults[app.name].imageDigest.slice(0, 16)}&hellip;</span>
              )}
              {status === "done" && <CheckCircle className="h-4 w-4 text-green-500" />}
            </div>
          </div>
        ))}
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />{error}
        </div>
      )}

      {status === "done" ? (
        <p className="text-sm text-green-600 flex items-center gap-2">
          <CheckCircle className="h-4 w-4" />Deployed successfully.
        </p>
      ) : (
        <button
          onClick={handleDeploy}
          disabled={!selectedCluster || status === "deploying"}
          className="w-full px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {status === "deploying" ? (
            <><Loader2 className="h-4 w-4 animate-spin" />Deploying&hellip;</>
          ) : "Deploy"}
        </button>
      )}
    </div>
  );
}
