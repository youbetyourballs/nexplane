import React, { useState, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Props {
  onClose: () => void;
}

export default function CreateCampaignDrawer({ onClose }: Props) {
  const qc = useQueryClient();
  const [cveInput, setCveInput] = useState("");
  const [searchedCve, setSearchedCve] = useState<string | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<Set<string>>(new Set());
  const [batchSize, setBatchSize] = useState(5);
  const [healthGate, setHealthGate] = useState(120);
  const [strategy, setStrategy] = useState("rolling");
  const [abortThreshold, setAbortThreshold] = useState(20);

  const { data: blastRadius, isFetching } = useQuery({
    queryKey: ["blast-radius", searchedCve],
    queryFn: () =>
      apiClient.get(`/api/v1/vulnerability/cve/${searchedCve}/blast-radius`).then(r => r.data),
    enabled: !!searchedCve,
    // No onSuccess — React Query v5
  });

  // Pre-select all affected assets when blast radius loads
  useEffect(() => {
    if (blastRadius?.affected_assets) {
      setSelectedAssets(new Set(blastRadius.affected_assets.map((a: any) => a.asset_id)));
    }
  }, [blastRadius]);

  const createMutation = useMutation({
    mutationFn: () =>
      apiClient.post("/api/v1/vulnerability/campaigns", {
        title: `Patch ${searchedCve || "campaign"} — ${new Date().toLocaleDateString()}`,
        cve_id: searchedCve,
        target_asset_ids: Array.from(selectedAssets),
        batch_size: batchSize,
        health_gate_seconds: healthGate,
        abort_threshold: abortThreshold / 100,
        rollout_strategy: strategy,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["campaigns"] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="w-full max-w-lg bg-white h-full shadow-xl overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-900">New Patch Campaign</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-xl">✕</button>
        </div>

        <div className="px-6 py-4 space-y-5">
          {/* CVE Search */}
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1.5">CVE / Package</label>
            <div className="flex gap-2">
              <input
                className="flex-1 border border-slate-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="CVE-2024-1234 or openssl"
                value={cveInput}
                onChange={e => setCveInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && setSearchedCve(cveInput.trim().toUpperCase())}
              />
              <button
                onClick={() => setSearchedCve(cveInput.trim().toUpperCase())}
                disabled={!cveInput.trim() || isFetching}
                className="px-3 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50"
              >
                {isFetching ? "…" : "Search"}
              </button>
            </div>
          </div>

          {/* Blast Radius Results */}
          {blastRadius && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-slate-700">
                  {blastRadius.total_affected} affected asset{blastRadius.total_affected !== 1 ? "s" : ""}
                </span>
                <button
                  onClick={() =>
                    setSelectedAssets(
                      selectedAssets.size === blastRadius.affected_assets.length
                        ? new Set()
                        : new Set(blastRadius.affected_assets.map((a: any) => a.asset_id))
                    )
                  }
                  className="text-xs text-blue-600 hover:underline"
                >
                  {selectedAssets.size === blastRadius.affected_assets.length ? "Deselect all" : "Select all"}
                </button>
              </div>
              <div className="border border-slate-200 rounded overflow-hidden max-h-48 overflow-y-auto">
                {blastRadius.affected_assets.map((a: any) => (
                  <label key={a.asset_id} className="flex items-center gap-2 px-3 py-2 hover:bg-slate-50 cursor-pointer border-b border-slate-100 last:border-0">
                    <input
                      type="checkbox"
                      checked={selectedAssets.has(a.asset_id)}
                      onChange={() => {
                        const next = new Set(selectedAssets);
                        if (next.has(a.asset_id)) next.delete(a.asset_id);
                        else next.add(a.asset_id);
                        setSelectedAssets(next);
                      }}
                    />
                    <span className="text-sm">{a.hostname || a.asset_id.slice(0, 8)}</span>
                    {a.installed_version && (
                      <span className="text-xs text-slate-400 font-mono ml-auto">{a.installed_version}</span>
                    )}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Config */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Batch size</label>
              <input type="number" min={1} max={50} value={batchSize}
                onChange={e => setBatchSize(Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Health gate (seconds)</label>
              <input type="number" min={10} value={healthGate}
                onChange={e => setHealthGate(Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Rollout strategy</label>
              <select value={strategy} onChange={e => setStrategy(e.target.value)}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm bg-white">
                <option value="rolling">Rolling</option>
                <option value="canary">Canary (1 first)</option>
                <option value="all_at_once">All at once</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1.5">Abort if failure % &gt;</label>
              <input type="number" min={1} max={100} value={abortThreshold}
                onChange={e => setAbortThreshold(Number(e.target.value))}
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm" />
            </div>
          </div>

          {/* Launch */}
          <button
            onClick={() => createMutation.mutate()}
            disabled={selectedAssets.size === 0 || createMutation.isPending}
            className="w-full py-2.5 bg-blue-600 text-white rounded font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {createMutation.isPending ? "Creating…" : `Launch Campaign → ${selectedAssets.size} asset${selectedAssets.size !== 1 ? "s" : ""}`}
          </button>
          {createMutation.isError && (
            <p className="text-sm text-red-600">Failed to create campaign. Please try again.</p>
          )}
        </div>
      </div>
    </div>
  );
}
