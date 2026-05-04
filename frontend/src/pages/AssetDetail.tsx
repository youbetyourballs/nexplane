import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Edit, Save, X, Plus, Zap } from "lucide-react";
import { assetsApi } from "../api/endpoints";
import { changeRequestsApi } from "../api/endpoints";
import { RiskBadge } from "../components/RiskBadge";
import { StatusBadge } from "../components/StatusBadge";
import { PageLoading } from "../components/LoadingSpinner";
import type { Asset, AssetType, Criticality } from "../types/api";

// Maps asset type → eligible change types with label + title/description templates
const ASSET_ACTIONS: Record<AssetType, { changeType: string; label: string; title: (a: Asset) => string; description: (a: Asset) => string }[]> = {
  server: [
    {
      changeType: "ec2_reboot",
      label: "Reboot Instance",
      title: (a) => `Reboot ${a.name}`,
      description: (a) => `Reboot EC2 instance ${a.asset_metadata?.instance_id ?? a.name} to apply pending changes.`,
    },
    {
      changeType: "ec2_stop",
      label: "Stop Instance",
      title: (a) => `Stop ${a.name}`,
      description: (a) => `Gracefully stop EC2 instance ${a.asset_metadata?.instance_id ?? a.name}.`,
    },
    {
      changeType: "ec2_start",
      label: "Start Instance",
      title: (a) => `Start ${a.name}`,
      description: (a) => `Start stopped EC2 instance ${a.asset_metadata?.instance_id ?? a.name}.`,
    },
    {
      changeType: "ec2_stop_start",
      label: "Restart Instance",
      title: (a) => `Restart ${a.name}`,
      description: (a) => `Full power cycle of EC2 instance ${a.asset_metadata?.instance_id ?? a.name}.`,
    },
    {
      changeType: "ec2_terminate",
      label: "Terminate Instance",
      title: (a) => `Terminate ${a.name}`,
      description: (a) => `Permanently terminate EC2 instance ${a.asset_metadata?.instance_id ?? a.name}. Irreversible.`,
    },
    {
      changeType: "snapshot_asset",
      label: "Snapshot",
      title: (a) => `Snapshot ${a.name}`,
      description: (a) => `Create a point-in-time snapshot of ${a.name} (${a.asset_metadata?.instance_id ?? ""}).`,
    },
    {
      changeType: "patch_packages",
      label: "Patch Packages",
      title: (a) => `Patch ${a.name}`,
      description: (a) => `Apply security patches to ${a.name}.`,
    },
    {
      changeType: "remote_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute an approved command template on ${a.name}.`,
    },
    {
      changeType: "isolate_host",
      label: "Isolate Host",
      title: (a) => `Isolate ${a.name}`,
      description: (a) => `Flush outbound firewall rules to isolate ${a.name} from the network.`,
    },
    {
      changeType: "enforce_cis_benchmark",
      label: "Enforce CIS Benchmark",
      title: (a) => `CIS Benchmark on ${a.name}`,
      description: (a) => `Audit and remediate CIS controls on ${a.name}.`,
    },
  ],
  cloud_account: [
    {
      changeType: "ec2_launch",
      label: "Launch EC2 Instance",
      title: (a) => `Launch EC2 in ${a.name}`,
      description: (a) => `Launch a new EC2 instance in AWS account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
    {
      changeType: "s3_block_public_access",
      label: "Block S3 Public Access",
      title: (a) => `Block S3 public access in ${a.name}`,
      description: (a) => `Enable S3 Block Public Access settings for account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
    {
      changeType: "iam_enforce_mfa",
      label: "Enforce IAM MFA",
      title: (a) => `Enforce MFA in ${a.name}`,
      description: (a) => `Enforce MFA requirement on IAM users in account ${a.asset_metadata?.account_id ?? a.name}.`,
    },
  ],
  dns_zone: [
    {
      changeType: "dns_update",
      label: "Update DNS Record",
      title: (a) => `Update DNS record in ${a.name}`,
      description: (a) => `Update a DNS record in zone ${a.name}.`,
    },
    {
      changeType: "dr_failover",
      label: "DR Failover",
      title: (a) => `DR failover for ${a.name}`,
      description: (a) => `Fail over to DR site via Route53 for zone ${a.name}.`,
    },
  ],
  firewall: [
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group on ${a.name}`,
      description: (a) => `Modify firewall rules on ${a.name}.`,
    },
    {
      changeType: "microsegmentation_policy",
      label: "Microsegmentation Policy",
      title: (a) => `Microsegmentation policy for ${a.name}`,
      description: (a) => `Stage a network microsegmentation policy on ${a.name}.`,
    },
  ],
  identity: [
    {
      changeType: "offboard_user",
      label: "Offboard User",
      title: (a) => `Offboard ${a.name}`,
      description: (a) => `Disable ${a.name} across all connected identity systems.`,
    },
    {
      changeType: "lockdown_account",
      label: "Lockdown Account",
      title: (a) => `Lockdown ${a.name}`,
      description: (a) => `Lock ${a.name} across all identity systems immediately.`,
    },
  ],
  identity_provider: [
    {
      changeType: "rotate_service_account",
      label: "Rotate Service Account",
      title: (a) => `Rotate service account on ${a.name}`,
      description: (a) => `Rotate a service account credential on ${a.name}.`,
    },
  ],
  application: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Upgrade ${a.name}`,
      description: (a) => `Upgrade Helm release for ${a.name}.`,
    },
    {
      changeType: "ansible_playbook",
      label: "Ansible Playbook",
      title: (a) => `Run Ansible on ${a.name}`,
      description: (a) => `Run an Ansible playbook against ${a.name}.`,
    },
  ],
  database: [
    {
      changeType: "rotate_db_credentials",
      label: "Rotate Credentials",
      title: (a) => `Rotate credentials on ${a.name}`,
      description: (a) => `Rotate database credentials for ${a.name} (${a.asset_metadata?.engine ?? "database"}).`,
    },
    {
      changeType: "create_backup",
      label: "Create Backup",
      title: (a) => `Backup ${a.name}`,
      description: (a) => `Create a backup snapshot of database ${a.name}.`,
    },
    {
      changeType: "provision_db_user",
      label: "Provision DB User",
      title: (a) => `Provision user on ${a.name}`,
      description: (a) => `Create a new database user on ${a.name}.`,
    },
    {
      changeType: "configure_db_audit",
      label: "Configure Audit Logging",
      title: (a) => `Configure audit on ${a.name}`,
      description: (a) => `Enable audit logging on database ${a.name}.`,
    },
    {
      changeType: "promote_db_replica",
      label: "Promote Replica",
      title: (a) => `Promote ${a.name} to primary`,
      description: (a) => `Promote ${a.name} read replica to standalone primary.`,
    },
  ],
  storage_bucket: [
    {
      changeType: "s3_block_public_access",
      label: "Block Public Access",
      title: (a) => `Block public access on ${a.name}`,
      description: (a) => `Enable S3 Block Public Access on bucket ${a.asset_metadata?.bucket_name ?? a.name}.`,
    },
    {
      changeType: "create_backup",
      label: "Create Backup",
      title: (a) => `Backup ${a.name}`,
      description: (a) => `Create a backup of bucket ${a.name}.`,
    },
  ],
  load_balancer: [
    {
      changeType: "security_group_update",
      label: "Update Security Group",
      title: (a) => `Update security group on ${a.name}`,
      description: (a) => `Modify security group rules for load balancer ${a.name}.`,
    },
    {
      changeType: "snapshot_asset",
      label: "Snapshot Config",
      title: (a) => `Snapshot ${a.name} config`,
      description: (a) => `Capture current configuration of load balancer ${a.name}.`,
    },
  ],
  endpoint: [
    {
      changeType: "isolate_host",
      label: "Isolate Host",
      title: (a) => `Isolate ${a.name}`,
      description: (a) => `Network-isolate endpoint ${a.name} (device_id: ${a.asset_metadata?.device_id ?? "unknown"}).`,
    },
    {
      changeType: "patch_packages",
      label: "Patch Packages",
      title: (a) => `Patch ${a.name}`,
      description: (a) => `Apply security patches to endpoint ${a.name}.`,
    },
    {
      changeType: "telemetry_agent_deploy",
      label: "Deploy Agent",
      title: (a) => `Deploy agent to ${a.name}`,
      description: (a) => `Deploy telemetry or security agent to endpoint ${a.name}.`,
    },
    {
      changeType: "remote_command",
      label: "Run Command",
      title: (a) => `Run command on ${a.name}`,
      description: (a) => `Execute an approved command on endpoint ${a.name}.`,
    },
    {
      changeType: "enforce_cis_benchmark",
      label: "Enforce CIS Benchmark",
      title: (a) => `CIS benchmark on ${a.name}`,
      description: (a) => `Audit and remediate CIS controls on endpoint ${a.name}.`,
    },
  ],
  container_cluster: [
    {
      changeType: "helm_upgrade",
      label: "Helm Upgrade",
      title: (a) => `Helm upgrade on ${a.name}`,
      description: (a) => `Upgrade a Helm release on cluster ${a.name}.`,
    },
    {
      changeType: "rolling_restart",
      label: "Rolling Restart",
      title: (a) => `Rolling restart on ${a.name}`,
      description: (a) => `Rolling restart of services on cluster ${a.name}.`,
    },
  ],
};

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

          {(ASSET_ACTIONS[asset.asset_type] ?? []).length > 0 && (
            <div className="bg-white border border-slate-200 rounded-lg p-5">
              <h2 className="text-sm font-semibold text-slate-900 mb-3 flex items-center gap-1.5">
                <Zap className="w-4 h-4 text-brand-500" /> Quick Actions
              </h2>
              <div className="space-y-1.5">
                {(ASSET_ACTIONS[asset.asset_type] ?? []).map((action) => (
                  <button
                    key={action.changeType}
                    onClick={() => {
                      const params = new URLSearchParams({
                        changeType: action.changeType,
                        assetId: asset.id,
                        title: action.title(asset),
                        description: action.description(asset),
                      });
                      navigate(`/change-requests/new?${params.toString()}`);
                    }}
                    className="w-full text-left px-3 py-2 text-sm rounded-md border border-slate-200 hover:border-brand-300 hover:bg-brand-50 text-slate-700 hover:text-brand-800 transition-colors"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>
          )}

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
