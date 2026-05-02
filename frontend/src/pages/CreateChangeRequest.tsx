import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { changeRequestsApi, assetsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import type { ChangeType } from "../types/api";

const CHANGE_TYPE_META: Record<ChangeType, { label: string; description: string; outcomeTemplate: string }> = {
  dns_update: {
    label: "DNS Update",
    description: "Update a DNS record (A, CNAME, MX, TXT)",
    outcomeTemplate: JSON.stringify({ record_name: "api.example.com", record_type: "A", new_value: "203.0.113.1", ttl: 300, rollback_strategy: "restore_previous_record" }, null, 2),
  },
  snapshot_asset: {
    label: "Snapshot Asset",
    description: "Create a point-in-time snapshot of a cloud asset",
    outcomeTemplate: JSON.stringify({ snapshot_tag: "pre-deployment-2026", rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  security_group_update: {
    label: "Security Group Update",
    description: "Modify firewall or cloud security group rules",
    outcomeTemplate: JSON.stringify({ group_id: "sg-abc123", rules: [{ action: "add", protocol: "tcp", port: 443, cidr: "10.0.0.0/8" }], rollback_strategy: "restore_rule_snapshot" }, null, 2),
  },
  key_rotation: {
    label: "Key Rotation",
    description: "Rotate API keys, secrets, or credentials",
    outcomeTemplate: JSON.stringify({ key_type: "api_key", service: "payment-service", consumers: ["app-1", "app-2"], grace_period_hours: 24, rollback_strategy: "cancel_revocation" }, null, 2),
  },
  telemetry_agent_deploy: {
    label: "Telemetry Agent Deploy",
    description: "Deploy logging, metrics, or security agent to hosts",
    outcomeTemplate: JSON.stringify({ agent_type: "filebeat", agent_version: "8.12.0", agent_config: { output: "elasticsearch", index: "logs-*" }, rollback_strategy: "uninstall_agent" }, null, 2),
  },
  remote_command: {
    label: "Remote Command",
    description: "Execute an approved command template on target hosts",
    outcomeTemplate: JSON.stringify({ template_id: "restart_service", parameters: { service_name: "nginx" }, rollback_strategy: "rollback_unavailable" }, null, 2),
  },
  microsegmentation_policy: {
    label: "Microsegmentation Policy",
    description: "Stage a network microsegmentation policy (simulation mode)",
    outcomeTemplate: JSON.stringify({ policy_rules: [{ src: "app-tier", dst: "db-tier", port: 5432, action: "allow" }, { src: "app-tier", dst: "internet", port: "any", action: "deny" }], critical_flows: [{ name: "app-to-db", src: "app-tier", dst: "db-tier", port: 5432 }], rollback_strategy: "remove_staged_policy" }, null, 2),
  },
  ec2_stop: {
    label: "Stop EC2 Instance",
    description: "Gracefully stop a running instance. Takes an EBS snapshot first as a safety net.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-stop-nexplane",
    }, null, 2),
  },
  ec2_start: {
    label: "Start EC2 Instance",
    description: "Start a stopped EC2 instance.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_reboot: {
    label: "Reboot EC2 Instance",
    description: "Soft reboot — stays on the same host, keeps its public IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_stop_start: {
    label: "Restart EC2 Instance",
    description: "Full power cycle (stop then start). Instance may get a new public IP if not using an Elastic IP.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
    }, null, 2),
  },
  ec2_launch: {
    label: "Launch EC2 Instance",
    description: "Launch a new instance. Set mode to 'quick' (free-tier defaults), 'clone' (copy existing), or 'spec' (full parameters).",
    outcomeTemplate: JSON.stringify({
      mode: "quick",
      name: "my-new-instance",
      os: "amazon_linux",
    }, null, 2),
  },
  ec2_terminate: {
    label: "Terminate EC2 Instance",
    description: "Permanently terminate an instance. Takes a mandatory snapshot first. Irreversible.",
    outcomeTemplate: JSON.stringify({
      instance_id: "i-0123456789abcdef0",
      snapshot_tag: "pre-terminate-nexplane",
      confirm_terminate: true,
    }, null, 2),
  },
};

export function CreateChangeRequest() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [changeType, setChangeType] = useState<ChangeType | "">("");
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [outcomeJson, setOutcomeJson] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [submitError, setSubmitError] = useState("");

  const { data: assets } = useQuery({
    queryKey: ["assets"],
    queryFn: () => assetsApi.list(),
  });

  const mutation = useMutation({
    mutationFn: () => {
      let outcome: Record<string, unknown>;
      try {
        outcome = JSON.parse(outcomeJson);
      } catch {
        throw new Error("Invalid JSON in Desired Outcome");
      }
      return changeRequestsApi.create({
        title,
        description,
        change_type: changeType as ChangeType,
        target_asset_ids: selectedAssets,
        desired_outcome: outcome,
      });
    },
    onSuccess: (cr) => {
      qc.invalidateQueries({ queryKey: ["change-requests"] });
      navigate(`/change-requests/${cr.id}`);
    },
    onError: (e: any) => setSubmitError(e.message),
  });

  function handleTypeChange(type: ChangeType) {
    setChangeType(type);
    setOutcomeJson(CHANGE_TYPE_META[type].outcomeTemplate);
    setJsonError("");
  }

  function handleJsonChange(value: string) {
    setOutcomeJson(value);
    try {
      JSON.parse(value);
      setJsonError("");
    } catch {
      setJsonError("Invalid JSON");
    }
  }

  const toggleAsset = (id: string) => {
    setSelectedAssets((prev) =>
      prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]
    );
  };

  const canSubmit = title && changeType && selectedAssets.length > 0 && outcomeJson && !jsonError;

  return (
    <div className="p-8 max-w-3xl">
      <PageHeader title="New Change Request" subtitle="Submit a governed infrastructure change for safety review and approval" />

      <div className="space-y-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Title</label>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Update DNS A record for api.acme.example"
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Describe the business reason for this change"
            rows={3}
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Change Type</label>
          <div className="grid grid-cols-2 gap-2">
            {(Object.keys(CHANGE_TYPE_META) as ChangeType[]).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => handleTypeChange(type)}
                className={`text-left p-3 rounded-lg border text-sm transition-colors ${
                  changeType === type
                    ? "border-brand-500 bg-brand-50 text-brand-800"
                    : "border-slate-200 hover:border-slate-300 text-slate-700"
                }`}
              >
                <div className="font-medium">{CHANGE_TYPE_META[type].label}</div>
                <div className="text-xs text-slate-400 mt-0.5">{CHANGE_TYPE_META[type].description}</div>
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-2">Target Assets</label>
          <div className="space-y-1.5 max-h-48 overflow-y-auto border border-slate-200 rounded-md p-2">
            {(assets ?? []).map((asset) => (
              <label key={asset.id} className="flex items-center gap-2.5 p-2 rounded hover:bg-slate-50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedAssets.includes(asset.id)}
                  onChange={() => toggleAsset(asset.id)}
                  className="rounded border-slate-300"
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-slate-900 truncate">{asset.name}</div>
                  <div className="text-xs text-slate-400">
                    {asset.asset_type.replace(/_/g, " ")} · {asset.environment} · {asset.criticality}
                  </div>
                </div>
              </label>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            Desired Outcome{" "}
            <span className="text-slate-400 font-normal">(JSON)</span>
          </label>
          <textarea
            value={outcomeJson}
            onChange={(e) => handleJsonChange(e.target.value)}
            rows={12}
            className={`w-full text-xs font-mono border rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none ${
              jsonError ? "border-red-300" : "border-slate-200"
            }`}
            placeholder="Select a change type above to load a template"
          />
          {jsonError && <div className="text-xs text-red-600 mt-1">{jsonError}</div>}
        </div>

        {submitError && (
          <div className="p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
            {submitError}
          </div>
        )}

        <div className="flex gap-3">
          <button
            type="button"
            onClick={() => mutation.mutate()}
            disabled={!canSubmit || mutation.isPending}
            className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {mutation.isPending ? "Creating..." : "Create Change Request"}
          </button>
          <button
            type="button"
            onClick={() => navigate(-1)}
            className="px-4 py-2 border border-slate-200 text-slate-600 text-sm rounded-md hover:bg-slate-50"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
