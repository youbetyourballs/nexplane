import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, Server } from "lucide-react";
import { assetsApi } from "../api/endpoints";
import { RiskBadge } from "../components/RiskBadge";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import type { AssetType, Environment, Criticality } from "../types/api";

const ASSET_TYPE_ICONS: Record<AssetType, string> = {
  server: "🖥",
  cloud_account: "☁️",
  dns_zone: "🌐",
  firewall: "🛡",
  identity_provider: "🔑",
  application: "📦",
};

export function Assets() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [assetType, setAssetType] = useState<AssetType>("server");
  const [environment, setEnvironment] = useState<Environment>("dev");
  const [criticality, setCriticality] = useState<Criticality>("medium");

  const { data, isLoading } = useQuery({
    queryKey: ["assets"],
    queryFn: assetsApi.list,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      assetsApi.create({ name, asset_type: assetType, environment, criticality }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      setShowForm(false);
      setName("");
    },
  });

  if (isLoading) return <PageLoading />;

  const groupedByEnv = {
    prod: (data ?? []).filter((a) => a.environment === "prod"),
    staging: (data ?? []).filter((a) => a.environment === "staging"),
    dev: (data ?? []).filter((a) => a.environment === "dev"),
  };

  return (
    <div className="p-8">
      <PageHeader
        title="Asset Inventory"
        subtitle={`${data?.length ?? 0} assets across all environments`}
        actions={
          <button
            onClick={() => setShowForm(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            <Plus className="w-4 h-4" />
            Add Asset
          </button>
        }
      />

      {showForm && (
        <div className="mb-6 bg-white border border-slate-200 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Add Asset</h3>
          <div className="grid grid-cols-2 gap-3 mb-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Type</label>
              <select value={assetType} onChange={(e) => setAssetType(e.target.value as AssetType)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["server","cloud_account","dns_zone","firewall","identity_provider","application"].map((t) => (
                  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Environment</label>
              <select value={environment} onChange={(e) => setEnvironment(e.target.value as Environment)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["dev","staging","prod"].map((e) => (
                  <option key={e} value={e}>{e}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Criticality</label>
              <select value={criticality} onChange={(e) => setCriticality(e.target.value as Criticality)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["low","medium","high","critical"].map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => createMutation.mutate()} disabled={!name || createMutation.isPending}
              className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50">
              {createMutation.isPending ? "Adding..." : "Add Asset"}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-3 py-1.5 border border-slate-200 text-slate-600 text-sm rounded hover:bg-slate-50">
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="space-y-6">
        {(["prod","staging","dev"] as Environment[]).map((env) => {
          const assets = groupedByEnv[env];
          if (assets.length === 0) return null;
          return (
            <div key={env}>
              <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">
                {env} ({assets.length})
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {assets.map((asset) => (
                  <div key={asset.id} className="bg-white border border-slate-200 rounded-lg p-4">
                    <div className="flex items-start justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{ASSET_TYPE_ICONS[asset.asset_type]}</span>
                        <div>
                          <div className="text-sm font-medium text-slate-900">{asset.name}</div>
                          <div className="text-xs text-slate-400">{asset.asset_type.replace(/_/g, " ")}</div>
                        </div>
                      </div>
                      <RiskBadge level={asset.criticality} size="sm" />
                    </div>
                    {Object.keys(asset.metadata).length > 0 && (
                      <div className="mt-2 pt-2 border-t border-slate-50">
                        {Object.entries(asset.metadata).slice(0, 3).map(([k, v]) => (
                          <div key={k} className="flex justify-between text-xs">
                            <span className="text-slate-400">{k}</span>
                            <span className="text-slate-600 font-mono">{String(v)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
