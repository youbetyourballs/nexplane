import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, useNavigate } from "react-router-dom";
import { Plus, Search, Tag, X, ChevronRight } from "lucide-react";
import { assetsApi, connectorsApi } from "../api/endpoints";
import { RiskBadge } from "../components/RiskBadge";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import type { AssetType, Environment, Criticality, Asset } from "../types/api";

const ASSET_TYPE_ICONS: Record<AssetType, string> = {
  server: "🖥",
  cloud_account: "☁️",
  dns_zone: "🌐",
  firewall: "🛡",
  identity_provider: "🔑",
  application: "📦",
  identity: "👤",
};

// Parses "payments env:prod tag:pci-scope" into { q: "payments", filters: { env: "prod", tag: "pci-scope" } }
function parseSearch(input: string): { q: string; filters: Record<string, string> } {
  const KEY_MAP: Record<string, string> = {
    env: "env",
    type: "asset_type",
    criticality: "criticality",
    tag: "tag",
    connector_id: "connector_id",
  };
  const filters: Record<string, string> = {};
  let remaining = input;
  const tokenRegex = /\b(\w+):(\S+)/g;
  let match;
  while ((match = tokenRegex.exec(input)) !== null) {
    const [full, key, value] = match;
    if (KEY_MAP[key]) {
      filters[KEY_MAP[key]] = value;
      remaining = remaining.replace(full, "").trim();
    }
  }
  return { q: remaining.trim(), filters };
}

function buildSearchString(q: string, filters: Record<string, string>): string {
  const REVERSE_MAP: Record<string, string> = {
    env: "env",
    asset_type: "type",
    criticality: "criticality",
    tag: "tag",
    connector_id: "connector_id",
  };
  const tokens = Object.entries(filters)
    .filter(([, v]) => v)
    .map(([k, v]) => `${REVERSE_MAP[k] ?? k}:${v}`);
  return [q, ...tokens].filter(Boolean).join(" ");
}

export function Assets() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<AssetType>("server");
  const [newEnv, setNewEnv] = useState<Environment>("dev");
  const [newCrit, setNewCrit] = useState<Criticality>("medium");

  // Bulk selection
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [showBulkAdd, setShowBulkAdd] = useState(false);
  const [showBulkRemove, setShowBulkRemove] = useState(false);
  const [bulkTagInput, setBulkTagInput] = useState("");

  // Local input state — decoupled from URL so keystrokes don't re-mount the DOM
  const [inputValue, setInputValue] = useState(() => searchParams.get("search") ?? "");

  // Sync URL → input when URL changes externally (browser back/forward)
  useEffect(() => {
    const urlValue = searchParams.get("search") ?? "";
    if (urlValue !== inputValue) setInputValue(urlValue);
  }, [searchParams]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounce input → URL so the query and layout switch fire after typing pauses
  useEffect(() => {
    const timer = setTimeout(() => {
      if (inputValue) {
        setSearchParams({ search: inputValue });
      } else {
        setSearchParams({});
      }
      setSelected(new Set());
    }, 300);
    return () => clearTimeout(timer);
  }, [inputValue]); // eslint-disable-line react-hooks/exhaustive-deps

  // Derive filter params from URL
  const rawSearch = searchParams.get("search") ?? "";
  const { q, filters } = parseSearch(rawSearch);
  const apiParams = { ...(q && { q }), ...filters };
  const hasFilters = rawSearch.length > 0;

  const { data: assets, isLoading } = useQuery({
    queryKey: ["assets", apiParams],
    queryFn: () => assetsApi.list(apiParams),
  });

  const { data: allTags } = useQuery({
    queryKey: ["asset-tags"],
    queryFn: () => assetsApi.tags(),
  });

  const { data: connectors } = useQuery({
    queryKey: ["connectors"],
    queryFn: connectorsApi.list,
    staleTime: 60000,
  });

  const createMutation = useMutation({
    mutationFn: () =>
      assetsApi.create({ name: newName, asset_type: newType, environment: newEnv, criticality: newCrit }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      setShowForm(false);
      setNewName("");
    },
  });

  const bulkTagMutation = useMutation({
    mutationFn: ({ operation, tags }: { operation: "add" | "remove" | "set"; tags: string[] }) =>
      assetsApi.bulkTag({ asset_ids: Array.from(selected), operation, tags }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset-tags"] });
      setSelected(new Set());
      setShowBulkAdd(false);
      setShowBulkRemove(false);
      setBulkTagInput("");
    },
  });

  function setSearch(value: string) {
    if (value) {
      setSearchParams({ search: value });
    } else {
      setSearchParams({});
    }
    setSelected(new Set());
  }

  function setFilter(key: string, value: string) {
    const newFilters = { ...filters, [key]: value };
    if (!value) delete newFilters[key];
    setSearch(buildSearchString(q, newFilters));
  }

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    if (!assets) return;
    if (selected.size === assets.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(assets.map((a) => a.id)));
    }
  }

  const selectedAssets = (assets ?? []).filter((a) => selected.has(a.id));
  const selectedTagUnion = Array.from(new Set(selectedAssets.flatMap((a) => a.tags ?? [])));

  if (isLoading) return <PageLoading />;

  const groupedByEnv = {
    prod: (assets ?? []).filter((a) => a.environment === "prod"),
    staging: (assets ?? []).filter((a) => a.environment === "staging"),
    dev: (assets ?? []).filter((a) => a.environment === "dev"),
  };

  return (
    <div className="p-8">
      <PageHeader
        title="Asset Inventory"
        subtitle={`${assets?.length ?? 0} assets`}
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

      {/* Search bar + filter dropdowns */}
      <div className="mb-4 flex flex-wrap gap-2 items-center">
        <div className="relative flex-1 min-w-64">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input
            type="text"
            placeholder="Search assets… or use env:prod tag:pci-scope"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            className="w-full pl-9 pr-3 py-2 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          {rawSearch && (
            <button onClick={() => setInputValue("")} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
        <select
          value={filters.env ?? ""}
          onChange={(e) => setFilter("env", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All Environments</option>
          {["dev", "staging", "prod"].map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
        <select
          value={filters.asset_type ?? ""}
          onChange={(e) => setFilter("asset_type", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All Types</option>
          {["server", "cloud_account", "dns_zone", "firewall", "identity_provider", "application"].map((t) => (
            <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
          ))}
        </select>
        <select
          value={filters.criticality ?? ""}
          onChange={(e) => setFilter("criticality", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">All Criticalities</option>
          {["low", "medium", "high", "critical"].map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select
          value={filters.connector_id ?? ""}
          onChange={(e) => setFilter("connector_id", e.target.value)}
          className="text-sm border border-slate-200 rounded-md px-2 py-1.5 text-slate-600 bg-white focus:outline-none focus:ring-1 focus:ring-brand-500"
        >
          <option value="">All connectors</option>
          {(connectors ?? []).map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <div className="relative">
          <Tag className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            list="tag-options"
            placeholder="Filter by tag…"
            value={filters.tag ?? ""}
            onChange={(e) => setFilter("tag", e.target.value)}
            className="pl-7 pr-3 py-2 text-sm border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-500 w-44"
          />
          <datalist id="tag-options">
            {(allTags ?? []).map((t) => <option key={t} value={t} />)}
          </datalist>
        </div>
      </div>

      {/* Add asset form */}
      {showForm && (
        <div className="mb-6 bg-white border border-slate-200 rounded-lg p-5">
          <h3 className="text-sm font-semibold text-slate-900 mb-4">Add Asset</h3>
          <div className="grid grid-cols-2 gap-3 mb-4">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Name</label>
              <input value={newName} onChange={(e) => setNewName(e.target.value)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500" />
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Type</label>
              <select value={newType} onChange={(e) => setNewType(e.target.value as AssetType)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["server","cloud_account","dns_zone","firewall","identity_provider","application"].map((t) => (
                  <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Environment</label>
              <select value={newEnv} onChange={(e) => setNewEnv(e.target.value as Environment)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["dev","staging","prod"].map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Criticality</label>
              <select value={newCrit} onChange={(e) => setNewCrit(e.target.value as Criticality)}
                className="w-full text-sm border border-slate-200 rounded px-3 py-1.5">
                {["low","medium","high","critical"].map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => createMutation.mutate()} disabled={!newName || createMutation.isPending}
              className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50">
              {createMutation.isPending ? "Adding…" : "Add Asset"}
            </button>
            <button onClick={() => setShowForm(false)}
              className="px-3 py-1.5 border border-slate-200 text-slate-600 text-sm rounded hover:bg-slate-50">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Bulk toolbar */}
      {selected.size > 0 && (
        <div className="mb-4 flex items-center gap-3 bg-brand-50 border border-brand-200 rounded-lg px-4 py-2.5">
          <span className="text-sm font-medium text-brand-700">{selected.size} asset{selected.size > 1 ? "s" : ""} selected</span>
          <div className="flex gap-2 ml-2">
            <div className="relative">
              <button onClick={() => { setShowBulkAdd(!showBulkAdd); setShowBulkRemove(false); }}
                className="px-3 py-1 text-sm bg-white border border-slate-200 rounded hover:bg-slate-50">
                Add tags
              </button>
              {showBulkAdd && (
                <div className="absolute top-8 left-0 z-10 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-56">
                  <label className="block text-xs text-slate-500 mb-1">Tags to add (comma-separated)</label>
                  <input
                    list="tag-options-bulk"
                    value={bulkTagInput}
                    onChange={(e) => setBulkTagInput(e.target.value)}
                    placeholder="e.g. pci-scope, payments"
                    className="w-full text-sm border border-slate-200 rounded px-2 py-1 mb-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                  <datalist id="tag-options-bulk">
                    {(allTags ?? []).map((t) => <option key={t} value={t} />)}
                  </datalist>
                  <button
                    disabled={!bulkTagInput.trim() || bulkTagMutation.isPending}
                    onClick={() => {
                      const tags = bulkTagInput.split(",").map((t) => t.trim()).filter(Boolean);
                      if (tags.length) bulkTagMutation.mutate({ operation: "add", tags });
                    }}
                    className="w-full px-2 py-1 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50"
                  >
                    {bulkTagMutation.isPending ? "Applying…" : "Apply"}
                  </button>
                </div>
              )}
            </div>
            <div className="relative">
              <button onClick={() => { setShowBulkRemove(!showBulkRemove); setShowBulkAdd(false); }}
                className="px-3 py-1 text-sm bg-white border border-slate-200 rounded hover:bg-slate-50">
                Remove tags
              </button>
              {showBulkRemove && (
                <div className="absolute top-8 left-0 z-10 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-56">
                  <label className="block text-xs text-slate-500 mb-2">Tags on selected assets</label>
                  <div className="flex flex-wrap gap-1 mb-2">
                    {selectedTagUnion.length === 0 && (
                      <span className="text-xs text-slate-400">No tags on selected assets</span>
                    )}
                    {selectedTagUnion.map((t) => (
                      <button key={t}
                        onClick={() => bulkTagMutation.mutate({ operation: "remove", tags: [t] })}
                        className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 text-slate-700 rounded-full hover:bg-red-100 hover:text-red-700">
                        {t} <X className="w-3 h-3" />
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
          <button onClick={() => setSelected(new Set())}
            className="ml-auto text-sm text-slate-500 hover:text-slate-700">
            Clear selection
          </button>
        </div>
      )}

      {/* Asset list */}
      {hasFilters ? (
        <div>
          <div className="flex items-center gap-2 mb-2 px-1">
            <input type="checkbox"
              checked={selected.size === (assets?.length ?? 0) && (assets?.length ?? 0) > 0}
              onChange={toggleSelectAll}
              className="rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            />
            <span className="text-xs text-slate-400">Select all {assets?.length} results</span>
          </div>
          <div className="space-y-2">
            {(assets ?? []).map((asset) => (
              <AssetRow key={asset.id} asset={asset} selected={selected.has(asset.id)}
                onToggle={() => toggleSelected(asset.id)} onClick={() => navigate(`/assets/${asset.id}`)} />
            ))}
            {assets?.length === 0 && (
              <div className="text-center py-12 text-slate-400 text-sm">No assets match your search.</div>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          {(["prod", "staging", "dev"] as Environment[]).map((env) => {
            const envAssets = groupedByEnv[env];
            if (envAssets.length === 0) return null;
            return (
              <div key={env}>
                <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wide mb-3">
                  {env} ({envAssets.length})
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                  {envAssets.map((asset) => (
                    <AssetCard key={asset.id} asset={asset} selected={selected.has(asset.id)}
                      onToggle={() => toggleSelected(asset.id)} onClick={() => navigate(`/assets/${asset.id}`)} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

interface AssetRowProps { asset: Asset; selected: boolean; onToggle: () => void; onClick: () => void; }

function AssetRow({ asset, selected, onToggle, onClick }: AssetRowProps) {
  return (
    <div className={`flex items-center gap-3 bg-white border rounded-lg px-4 py-3 hover:border-brand-300 transition-colors ${selected ? "border-brand-300 bg-brand-50" : "border-slate-200"}`}>
      <input type="checkbox" checked={selected} onChange={onToggle} onClick={(e) => e.stopPropagation()}
        className="rounded border-slate-300 text-brand-600 focus:ring-brand-500 shrink-0" />
      <span className="text-lg">{ASSET_TYPE_ICONS[asset.asset_type]}</span>
      <button onClick={onClick} className="flex-1 text-left">
        <div className="text-sm font-medium text-slate-900">{asset.name}</div>
        <div className="text-xs text-slate-400">
          {asset.asset_type.replace(/_/g, " ")} · {asset.environment}
          {asset.connector_name && <span className="ml-1">· {asset.connector_name}</span>}
        </div>
      </button>
      <div className="flex flex-wrap gap-1">
        {(asset.tags ?? []).slice(0, 3).map((t) => (
          <span key={t} className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-600 rounded-full">{t}</span>
        ))}
        {(asset.tags ?? []).length > 3 && (
          <span className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-400 rounded-full">+{asset.tags.length - 3}</span>
        )}
      </div>
      <RiskBadge level={asset.criticality} size="sm" />
      <ChevronRight className="w-4 h-4 text-slate-300 shrink-0" />
    </div>
  );
}

interface AssetCardProps { asset: Asset; selected: boolean; onToggle: () => void; onClick: () => void; }

function AssetCard({ asset, selected, onToggle, onClick }: AssetCardProps) {
  return (
    <div className={`bg-white border rounded-lg p-4 hover:border-brand-300 transition-colors cursor-pointer ${selected ? "border-brand-300 bg-brand-50" : "border-slate-200"}`}>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <input type="checkbox" checked={selected} onChange={onToggle} onClick={(e) => e.stopPropagation()}
            className="rounded border-slate-300 text-brand-600 focus:ring-brand-500" />
          <span className="text-lg">{ASSET_TYPE_ICONS[asset.asset_type]}</span>
          <button onClick={onClick} className="text-left">
            <div className="text-sm font-medium text-slate-900">{asset.name}</div>
            <div className="text-xs text-slate-400">{asset.asset_type.replace(/_/g, " ")}</div>
          </button>
        </div>
        <RiskBadge level={asset.criticality} size="sm" />
      </div>
      {(asset.tags ?? []).length > 0 && (
        <div className="flex flex-wrap gap-1 mt-2">
          {asset.tags.map((t) => (
            <span key={t} className="px-1.5 py-0.5 text-xs bg-slate-100 text-slate-600 rounded-full">{t}</span>
          ))}
        </div>
      )}
    </div>
  );
}
