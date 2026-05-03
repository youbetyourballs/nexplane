import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Policy {
  id: string;
  name: string;
  match_scanner: string | null;
  match_finding_type: string | null;
  match_severity: string[] | null;
  match_resource_type: string | null;
  action_type: string;
  approval_level: string;
  priority: number;
  enabled: boolean;
  created_at: string;
}

const ACTION_TYPES = [
  "patch_packages",
  "s3_block_public_access",
  "security_group_update",
  "iam_enforce_mfa",
  "generic_remediation",
  "notify_only",
  "suppress",
];

const SEVERITIES = ["critical", "high", "medium", "low", "informational"];
const SCANNERS = ["qualys", "tenable", "wiz", "snyk", "crowdstrike"];
const FINDING_TYPES = ["cve", "misconfiguration", "secret", "iac"];

interface PolicyFormState {
  name: string;
  match_scanner: string;
  match_finding_type: string;
  match_severity: string[];
  match_resource_type: string;
  action_type: string;
  approval_level: string;
  priority: number;
  enabled: boolean;
}

const defaultForm = (): PolicyFormState => ({
  name: "",
  match_scanner: "",
  match_finding_type: "",
  match_severity: [],
  match_resource_type: "",
  action_type: "patch_packages",
  approval_level: "require_approval",
  priority: 0,
  enabled: true,
});

export default function RemediationPolicyEditor() {
  const [showModal, setShowModal] = useState(false);
  const [form, setForm] = useState<PolicyFormState>(defaultForm());
  const queryClient = useQueryClient();

  const { data: policies = [], isLoading } = useQuery<Policy[]>({
    queryKey: ["remediation-policies"],
    queryFn: () =>
      apiClient.get<Policy[]>("/api/v1/vulnerability/policies").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: (body: object) =>
      apiClient.post("/api/v1/vulnerability/policies", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["remediation-policies"] });
      setShowModal(false);
      setForm(defaultForm());
    },
  });

  const toggleMutation = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      apiClient.patch(`/api/v1/vulnerability/policies/${id}`, { enabled }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["remediation-policies"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiClient.delete(`/api/v1/vulnerability/policies/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["remediation-policies"] }),
  });

  const handleCreate = () => {
    const payload = {
      ...form,
      match_scanner: form.match_scanner || null,
      match_finding_type: form.match_finding_type || null,
      match_severity: form.match_severity.length ? form.match_severity : null,
      match_resource_type: form.match_resource_type || null,
    };
    createMutation.mutate(payload);
  };

  const toggleSeverity = (sev: string) => {
    setForm((f) => ({
      ...f,
      match_severity: f.match_severity.includes(sev)
        ? f.match_severity.filter((s) => s !== sev)
        : [...f.match_severity, sev],
    }));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Remediation Policies</h2>
        <button
          className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm hover:bg-blue-700"
          onClick={() => setShowModal(true)}
        >
          + New Rule
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-500 text-sm">Loading policies...</p>
      ) : policies.length === 0 ? (
        <p className="text-gray-500 text-sm">
          No remediation policies configured. Create one to start auto-generating change requests from findings.
        </p>
      ) : (
        <table className="w-full text-sm border rounded overflow-hidden">
          <thead className="bg-gray-50">
            <tr>
              <th className="text-left p-3 font-medium">Name</th>
              <th className="text-left p-3 font-medium">Matches</th>
              <th className="text-left p-3 font-medium">Action</th>
              <th className="text-left p-3 font-medium">Approval</th>
              <th className="text-left p-3 font-medium">Priority</th>
              <th className="text-left p-3 font-medium">Enabled</th>
              <th className="p-3"></th>
            </tr>
          </thead>
          <tbody>
            {[...policies].sort((a, b) => b.priority - a.priority).map((p) => (
              <tr key={p.id} className={`border-t ${p.enabled ? "" : "opacity-50"}`}>
                <td className="p-3 font-medium">{p.name}</td>
                <td className="p-3 text-xs text-gray-600 space-y-0.5">
                  {p.match_scanner && <div>Scanner: {p.match_scanner}</div>}
                  {p.match_finding_type && <div>Type: {p.match_finding_type}</div>}
                  {p.match_severity?.length && <div>Severity: {p.match_severity.join(", ")}</div>}
                  {!p.match_scanner && !p.match_finding_type && !p.match_severity && (
                    <div className="text-gray-400">Any finding</div>
                  )}
                </td>
                <td className="p-3">
                  <code className="text-xs bg-gray-100 px-1 rounded">{p.action_type}</code>
                </td>
                <td className="p-3 text-xs">
                  {p.approval_level === "auto" ? (
                    <span className="text-green-700 font-medium">Auto</span>
                  ) : (
                    <span className="text-gray-600">Require approval</span>
                  )}
                </td>
                <td className="p-3 text-center">{p.priority}</td>
                <td className="p-3">
                  <button
                    className={`w-10 h-5 rounded-full transition-colors ${p.enabled ? "bg-blue-600" : "bg-gray-300"}`}
                    onClick={() => toggleMutation.mutate({ id: p.id, enabled: !p.enabled })}
                  />
                </td>
                <td className="p-3">
                  <button
                    className="text-red-500 hover:text-red-700 text-xs"
                    onClick={() => {
                      if (confirm(`Delete policy "${p.name}"?`)) deleteMutation.mutate(p.id);
                    }}
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Create Policy Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-full max-w-lg space-y-4">
            <h3 className="text-lg font-semibold">New Remediation Policy</h3>

            <div>
              <label className="block text-sm font-medium mb-1">Rule Name</label>
              <input
                className="border rounded px-3 py-2 w-full text-sm"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Auto-patch critical CVEs"
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Match Scanner</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.match_scanner}
                  onChange={(e) => setForm((f) => ({ ...f, match_scanner: e.target.value }))}
                >
                  <option value="">Any</option>
                  {SCANNERS.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Match Finding Type</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.match_finding_type}
                  onChange={(e) => setForm((f) => ({ ...f, match_finding_type: e.target.value }))}
                >
                  <option value="">Any</option>
                  {FINDING_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Match Severity (leave empty for any)</label>
              <div className="flex gap-2 flex-wrap">
                {SEVERITIES.map((sev) => (
                  <button
                    key={sev}
                    type="button"
                    className={`px-2 py-0.5 rounded border text-xs ${
                      form.match_severity.includes(sev)
                        ? "bg-blue-600 text-white border-blue-600"
                        : "border-gray-300 text-gray-700"
                    }`}
                    onClick={() => toggleSeverity(sev)}
                  >
                    {sev}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Action Type</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.action_type}
                  onChange={(e) => setForm((f) => ({ ...f, action_type: e.target.value }))}
                >
                  {ACTION_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Approval Level</label>
                <select
                  className="border rounded px-2 py-1.5 w-full text-sm"
                  value={form.approval_level}
                  onChange={(e) => setForm((f) => ({ ...f, approval_level: e.target.value }))}
                >
                  <option value="require_approval">Require Approval</option>
                  <option value="auto">Auto (low-risk only)</option>
                </select>
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Priority (higher wins)</label>
              <input
                type="number"
                className="border rounded px-3 py-2 w-24 text-sm"
                value={form.priority}
                onChange={(e) => setForm((f) => ({ ...f, priority: Number(e.target.value) }))}
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                className="px-4 py-2 border rounded text-sm hover:bg-gray-50"
                onClick={() => { setShowModal(false); setForm(defaultForm()); }}
              >
                Cancel
              </button>
              <button
                className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
                onClick={handleCreate}
                disabled={!form.name || createMutation.isPending}
              >
                {createMutation.isPending ? "Creating..." : "Create Rule"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
