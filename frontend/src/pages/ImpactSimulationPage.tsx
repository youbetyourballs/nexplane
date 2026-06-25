import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Zap, Search, AlertTriangle, ArrowUp, ArrowDown, Clock } from "lucide-react";
import { impactSimulationApi } from "../api/endpoints";
import { assetsApi } from "../api/endpoints";
import { useDebounce } from "../hooks/useDebounce";

const CRIT_COLORS: Record<string, string> = {
  critical: "bg-red-500/10 text-red-400 border-red-400/30",
  high: "bg-orange-500/10 text-orange-400 border-orange-400/30",
  medium: "bg-amber-500/10 text-amber-400 border-amber-400/30",
  low: "bg-slate-500/10 text-slate-400 border-slate-400/30",
};

const STATUS_COLORS: Record<string, string> = {
  approved: "text-green-400",
  executed: "text-brand-400",
  pending: "text-amber-400",
  draft: "text-slate-400",
};

export function ImpactSimulationPage() {
  const [searchInput, setSearchInput] = useState("");
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [selectedAssetName, setSelectedAssetName] = useState<string>("");
  const [showDropdown, setShowDropdown] = useState(false);
  const debouncedSearch = useDebounce(searchInput, 300);

  const { data: searchResults } = useQuery({
    queryKey: ["asset-search-impact", debouncedSearch],
    queryFn: () =>
      assetsApi.list({ q: debouncedSearch }).then((r) =>
        Array.isArray(r) ? r : (r as { items?: unknown[] }).items ?? r
      ),
    enabled: debouncedSearch.length >= 2,
  });

  const { data: simResult, isLoading: simLoading } = useQuery({
    queryKey: ["impact-simulation", selectedAssetId],
    queryFn: () => impactSimulationApi.analyze(selectedAssetId!),
    enabled: !!selectedAssetId,
  });

  function selectAsset(id: string, name: string) {
    setSelectedAssetId(id);
    setSelectedAssetName(name);
    setSearchInput(name);
    setShowDropdown(false);
  }

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-2">
        <div className="p-2 bg-brand-500/10 rounded-lg">
          <Zap className="w-5 h-5 text-brand-400" />
        </div>
        <h1 className="text-2xl font-bold text-white">Impact Simulation</h1>
      </div>
      <p className="text-slate-400 mb-6">What will happen if I change this?</p>

      {/* Asset selector */}
      <div className="relative mb-8 max-w-lg">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
        <input
          type="text"
          placeholder="Search for an asset…"
          value={searchInput}
          onChange={(e) => {
            setSearchInput(e.target.value);
            setShowDropdown(true);
            if (e.target.value !== selectedAssetName) setSelectedAssetId(null);
          }}
          onFocus={() => setShowDropdown(true)}
          onBlur={() => setTimeout(() => setShowDropdown(false), 150)}
          className="w-full pl-9 pr-4 py-2 bg-navy-light border border-navy-border rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:border-brand-400"
        />
        {showDropdown && Array.isArray(searchResults) && searchResults.length > 0 && (
          <div className="absolute z-10 top-full left-0 right-0 mt-1 bg-navy-light border border-navy-border rounded-lg shadow-lg overflow-hidden">
            {(searchResults as Array<{ id: string; name: string; asset_type: string; environment: string }>).map((a) => (
              <button
                key={a.id}
                onMouseDown={() => selectAsset(a.id, a.name)}
                className="w-full text-left px-4 py-2.5 text-sm hover:bg-navy-border transition-colors flex items-center justify-between"
              >
                <span className="text-white font-medium">{a.name}</span>
                <span className="text-xs text-slate-500">{a.asset_type?.replace(/_/g, " ")}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Empty state */}
      {!selectedAssetId && (
        <div className="text-center py-20 text-slate-500">
          <Zap className="w-10 h-10 mx-auto mb-3 opacity-30" />
          <p className="text-base">Select an asset above to see its blast radius.</p>
        </div>
      )}

      {/* Loading */}
      {selectedAssetId && simLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-32 bg-navy-light border border-navy-border rounded-lg animate-pulse" />
          ))}
        </div>
      )}

      {/* Results */}
      {simResult && !simLoading && (
        <div className="space-y-4">
          {/* Risk banner */}
          {(simResult.downstream_risk.critical > 0 || simResult.downstream_risk.high > 0) && (
            <div className="flex items-center gap-3 px-4 py-3 bg-red-500/10 border border-red-400/30 rounded-lg">
              <AlertTriangle className="w-4 h-4 text-red-400 shrink-0" />
              <span className="text-sm text-red-300">
                Changing <strong>{simResult.asset.name}</strong> could affect{" "}
                {simResult.downstream_risk.critical > 0 && (
                  <strong>{simResult.downstream_risk.critical} critical</strong>
                )}
                {simResult.downstream_risk.critical > 0 && simResult.downstream_risk.high > 0 && " and "}
                {simResult.downstream_risk.high > 0 && (
                  <strong>{simResult.downstream_risk.high} high</strong>
                )}{" "}
                downstream asset{simResult.downstream_risk.total !== 1 ? "s" : ""}.
              </span>
            </div>
          )}

          {/* Asset info */}
          <div className="bg-navy-light border border-navy-border rounded-lg p-4">
            <div className="flex items-center justify-between">
              <div>
                <Link to={`/assets/${simResult.asset.id}`} className="text-lg font-semibold text-brand-400 hover:text-brand-300">
                  {simResult.asset.name}
                </Link>
                {simResult.asset.why_exists && (
                  <p className="text-sm text-slate-400 mt-0.5">{simResult.asset.why_exists}</p>
                )}
              </div>
              <span className={`text-xs px-2 py-0.5 rounded border ${CRIT_COLORS[simResult.asset.criticality] ?? ""}`}>
                {simResult.asset.criticality}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* Downstream */}
            <div className="bg-navy-light border border-navy-border rounded-lg p-4">
              <div className="flex items-center gap-2 mb-3">
                <ArrowDown className="w-4 h-4 text-red-400" />
                <h2 className="text-sm font-semibold text-slate-200">
                  Downstream impact ({simResult.downstream.length})
                </h2>
              </div>
              {simResult.downstream.length === 0 ? (
                <p className="text-xs text-slate-500">No downstream dependents.</p>
              ) : (
                <div className="space-y-1.5">
                  {simResult.downstream.map((d) => (
                    <div key={d.id} className="flex items-center justify-between">
                      <Link to={`/assets/${d.id}`} className="text-sm text-slate-300 hover:text-brand-400 truncate">
                        {d.name}
                      </Link>
                      <span className={`text-xs px-1.5 py-0.5 rounded border ml-2 shrink-0 ${CRIT_COLORS[d.criticality] ?? ""}`}>
                        {d.criticality}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Upstream */}
            <div className="bg-navy-light border border-navy-border rounded-lg p-4">
              <div className="flex items-center gap-2 mb-3">
                <ArrowUp className="w-4 h-4 text-brand-400" />
                <h2 className="text-sm font-semibold text-slate-200">
                  Dependencies ({simResult.upstream.length})
                </h2>
              </div>
              {simResult.upstream.length === 0 ? (
                <p className="text-xs text-slate-500">No upstream dependencies.</p>
              ) : (
                <div className="space-y-1.5">
                  {simResult.upstream.map((u) => (
                    <div key={u.id} className="flex items-center justify-between">
                      <Link to={`/assets/${u.id}`} className="text-sm text-slate-300 hover:text-brand-400 truncate">
                        {u.name}
                      </Link>
                      <span className="text-xs text-slate-500 ml-2 shrink-0">{u.relationship_type}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Recent CRs */}
          <div className="bg-navy-light border border-navy-border rounded-lg p-4">
            <div className="flex items-center gap-2 mb-3">
              <Clock className="w-4 h-4 text-slate-400" />
              <h2 className="text-sm font-semibold text-slate-200">Recent changes</h2>
            </div>
            {simResult.recent_crs.length === 0 ? (
              <p className="text-xs text-slate-500">No recent changes found.</p>
            ) : (
              <div className="space-y-1.5">
                {simResult.recent_crs.map((cr) => (
                  <div key={cr.id} className="flex items-center justify-between">
                    <Link to={`/change-requests/${cr.id}`} className="text-sm text-slate-300 hover:text-brand-400 truncate">
                      {cr.title}
                    </Link>
                    <span className={`text-xs ml-2 shrink-0 ${STATUS_COLORS[cr.status] ?? "text-slate-400"}`}>
                      {cr.status}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
