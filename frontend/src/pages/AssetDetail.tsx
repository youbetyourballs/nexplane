import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Edit, Save, X, Plus } from "lucide-react";
import { assetsApi } from "../api/endpoints";
import { changeRequestsApi } from "../api/endpoints";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { PageLoading } from "../components/LoadingSpinner";
import type { Criticality } from "../types/api";

export function AssetDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState("");
  const [editCriticality, setEditCriticality] = useState<Criticality>("medium");
  const [editTags, setEditTags] = useState<string[]>([]);
  const [editMetadata, setEditMetadata] = useState("");
  const [metadataError, setMetadataError] = useState("");
  const [tagInput, setTagInput] = useState("");

  const { data: asset, isLoading } = useQuery({
    queryKey: ["asset", id],
    queryFn: () => assetsApi.get(id!),
    enabled: !!id,
  });

  const { data: allTags } = useQuery({
    queryKey: ["asset-tags"],
    queryFn: assetsApi.tags,
  });

  const { data: linkedCRs } = useQuery({
    queryKey: ["change-requests", { asset_id: id }],
    queryFn: () => changeRequestsApi.list({ asset_id: id }),
    enabled: !!id,
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      let metadata: Record<string, unknown>;
      try {
        metadata = editMetadata.trim() ? JSON.parse(editMetadata) : {};
      } catch {
        setMetadataError("Invalid JSON");
        throw new Error("Invalid JSON");
      }
      return assetsApi.update(id!, {
        name: editName !== asset?.name ? editName : undefined,
        criticality: editCriticality !== asset?.criticality ? editCriticality : undefined,
        tags: editTags,
        asset_metadata: metadata,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["asset", id] });
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["asset-tags"] });
      setEditing(false);
    },
  });

  function startEdit() {
    if (!asset) return;
    setEditName(asset.name);
    setEditCriticality(asset.criticality);
    setEditTags([...(asset.tags ?? [])]);
    setEditMetadata(JSON.stringify(asset.asset_metadata, null, 2));
    setMetadataError("");
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
    setMetadataError("");
    setTagInput("");
  }

  function addTag() {
    const tag = tagInput.trim();
    if (tag && !editTags.includes(tag)) {
      setEditTags([...editTags, tag]);
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setEditTags(editTags.filter((t) => t !== tag));
  }

  if (isLoading) return <PageLoading />;
  if (!asset) return <div className="p-8 text-slate-500">Asset not found.</div>;

  return (
    <div className="p-8 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <button onClick={() => navigate(-1)}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded hover:bg-slate-100">
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-slate-900">{asset.name}</h1>
            <div className="text-sm text-slate-400">{asset.asset_type.replace(/_/g, " ")} · {asset.environment}</div>
            {asset.connector_name && (
              <div className="flex items-center gap-2 text-sm text-slate-500 mt-1">
                <span className="text-slate-400">Source connector:</span>
                <span className="font-medium text-slate-700">{asset.connector_name}</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {!editing ? (
            <button onClick={startEdit}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50">
              <Edit className="w-4 h-4" /> Edit
            </button>
          ) : (
            <>
              <button onClick={cancelEdit}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm border border-slate-200 rounded-md hover:bg-slate-50">
                <X className="w-4 h-4" /> Cancel
              </button>
              <button
                onClick={() => updateMutation.mutate()}
                disabled={updateMutation.isPending || !!metadataError}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-sm bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50">
                <Save className="w-4 h-4" />
                {updateMutation.isPending ? "Saving…" : "Save"}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Properties + Metadata */}
        <div className="lg:col-span-2 space-y-5">
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-4">Properties</h2>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3">
              <div>
                <dt className="text-xs text-slate-400">Name</dt>
                {editing ? (
                  <input value={editName} onChange={(e) => setEditName(e.target.value)}
                    className="mt-0.5 w-full text-sm border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500" />
                ) : (
                  <dd className="text-sm text-slate-900 font-medium mt-0.5">{asset.name}</dd>
                )}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Criticality</dt>
                {editing ? (
                  <select value={editCriticality} onChange={(e) => setEditCriticality(e.target.value as Criticality)}
                    className="mt-0.5 w-full text-sm border border-slate-200 rounded px-2 py-1">
                    {["low", "medium", "high", "critical"].map((c) => <option key={c} value={c}>{c}</option>)}
                  </select>
                ) : (
                  <dd className="mt-0.5"><RiskBadge level={asset.criticality} size="sm" /></dd>
                )}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Type</dt>
                <dd className="text-sm text-slate-900 mt-0.5">{asset.asset_type.replace(/_/g, " ")}</dd>
                {editing && <p className="text-xs text-slate-400 mt-0.5">Cannot be changed after creation.</p>}
              </div>
              <div>
                <dt className="text-xs text-slate-400">Environment</dt>
                <dd className="text-sm text-slate-900 mt-0.5">{asset.environment}</dd>
                {editing && <p className="text-xs text-slate-400 mt-0.5">Cannot be changed after creation.</p>}
              </div>
            </dl>
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Metadata</h2>
            {editing ? (
              <>
                <textarea
                  value={editMetadata}
                  onChange={(e) => { setEditMetadata(e.target.value); setMetadataError(""); }}
                  rows={8}
                  className={`w-full text-xs font-mono border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 ${metadataError ? "border-red-400" : "border-slate-200"}`}
                />
                {metadataError && <p className="text-xs text-red-500 mt-1">{metadataError}</p>}
              </>
            ) : (
              Object.keys(asset.asset_metadata).length === 0 ? (
                <p className="text-sm text-slate-400">No metadata.</p>
              ) : (
                <pre className="text-xs font-mono text-slate-700 bg-slate-50 rounded p-3 overflow-auto">
                  {JSON.stringify(asset.asset_metadata, null, 2)}
                </pre>
              )
            )}
          </div>
        </div>

        {/* Right: Tags + Change Requests */}
        <div className="space-y-5">
          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Tags</h2>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {(editing ? editTags : (asset.tags ?? [])).map((t) => (
                <span key={t}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-slate-100 text-slate-700 rounded-full">
                  {t}
                  {editing && (
                    <button onClick={() => removeTag(t)} className="text-slate-400 hover:text-red-500">
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </span>
              ))}
              {!editing && (asset.tags ?? []).length === 0 && (
                <span className="text-xs text-slate-400">No tags. Click Edit to add.</span>
              )}
            </div>
            {editing && (
              <div className="flex gap-1 mt-2">
                <input
                  list="tag-options-detail"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === ",") { e.preventDefault(); addTag(); } }}
                  placeholder="Add tag…"
                  className="flex-1 text-sm border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
                <datalist id="tag-options-detail">
                  {(allTags ?? []).map((t) => <option key={t} value={t} />)}
                </datalist>
                <button onClick={addTag}
                  className="p-1.5 bg-brand-600 text-white rounded hover:bg-brand-700">
                  <Plus className="w-4 h-4" />
                </button>
              </div>
            )}
          </div>

          <div className="bg-white border border-slate-200 rounded-lg p-5">
            <h2 className="text-sm font-semibold text-slate-900 mb-3">Change Requests</h2>
            {!linkedCRs || linkedCRs.length === 0 ? (
              <p className="text-xs text-slate-400">No change requests targeting this asset.</p>
            ) : (
              <div className="space-y-2">
                {linkedCRs.slice(0, 10).map((cr) => (
                  <button key={cr.id}
                    onClick={() => navigate(`/change-requests/${cr.id}`)}
                    className="w-full text-left p-2 rounded hover:bg-slate-50 border border-transparent hover:border-slate-200">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-slate-900 truncate">{cr.title}</span>
                      <StatusBadge status={cr.status} size="sm" />
                    </div>
                    <div className="text-xs text-slate-400 mt-0.5">
                      {new Date(cr.created_at).toLocaleDateString()}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
