// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Trash2,
  Edit2,
  ChevronUp,
  ChevronDown,
  ToggleLeft,
  ToggleRight,
  Eye,
  X,
} from "lucide-react";
import { apiClient } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

// ── types ─────────────────────────────────────────────────────────────────────

interface NotificationRule {
  id: string;
  name: string;
  enabled: boolean;
  priority: number;
  match_cr_types: string[] | null;
  match_severities: string[] | null;
  match_asset_tags: Record<string, string> | null;
  match_connector_types: string[] | null;
  match_status: string[] | null;
  notify_user_ids: string[] | null;
  notify_role_ids: string[] | null;
  notify_channels: string[] | null;
  suppress_default: boolean;
  created_at: string;
  created_by: string | null;
}

interface PreviewResult {
  user_ids: string[];
  channels: string[];
  suppress_default: boolean;
  matched_rules: string[];
}

// ── constants ─────────────────────────────────────────────────────────────────

const SEVERITY_OPTIONS = ["critical", "high", "medium", "low", "informational"];
const STATUS_OPTIONS = [
  "draft", "pending_approval", "approved", "executing",
  "completed", "failed", "rolled_back", "batch_running", "batch_aborted",
];
const ROLE_OPTIONS = ["admin", "security_operator", "approver", "auditor", "ir_responder"];

// ── empty form ────────────────────────────────────────────────────────────────

const EMPTY_FORM = {
  name: "",
  enabled: true,
  priority: 100,
  match_cr_types_raw: "",
  match_severities: [] as string[],
  match_asset_tags_raw: "",
  match_connector_types_raw: "",
  match_status: [] as string[],
  notify_user_ids_raw: "",
  notify_role_ids: [] as string[],
  notify_channels_raw: "",
  suppress_default: false,
};

type FormState = typeof EMPTY_FORM;

function ruleToForm(r: NotificationRule): FormState {
  return {
    name: r.name,
    enabled: r.enabled,
    priority: r.priority,
    match_cr_types_raw: (r.match_cr_types ?? []).join(", "),
    match_severities: r.match_severities ?? [],
    match_asset_tags_raw: r.match_asset_tags
      ? Object.entries(r.match_asset_tags)
          .map(([k, v]) => `${k}=${v}`)
          .join(", ")
      : "",
    match_connector_types_raw: (r.match_connector_types ?? []).join(", "),
    match_status: r.match_status ?? [],
    notify_user_ids_raw: (r.notify_user_ids ?? []).join(", "),
    notify_role_ids: r.notify_role_ids ?? [],
    notify_channels_raw: (r.notify_channels ?? []).join(", "),
    suppress_default: r.suppress_default,
  };
}

function formToPayload(f: FormState) {
  const csvToList = (s: string) =>
    s
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);

  const parseTagPairs = (s: string): Record<string, string> | null => {
    const pairs = csvToList(s);
    if (!pairs.length) return null;
    const obj: Record<string, string> = {};
    for (const p of pairs) {
      const [k, ...rest] = p.split("=");
      obj[k.trim()] = rest.join("=").trim();
    }
    return obj;
  };

  return {
    name: f.name,
    enabled: f.enabled,
    priority: f.priority,
    match_cr_types: csvToList(f.match_cr_types_raw) || null,
    match_severities: f.match_severities.length ? f.match_severities : null,
    match_asset_tags: parseTagPairs(f.match_asset_tags_raw),
    match_connector_types: csvToList(f.match_connector_types_raw) || null,
    match_status: f.match_status.length ? f.match_status : null,
    notify_user_ids: csvToList(f.notify_user_ids_raw) || null,
    notify_role_ids: f.notify_role_ids.length ? f.notify_role_ids : null,
    notify_channels: csvToList(f.notify_channels_raw) || null,
    suppress_default: f.suppress_default,
  };
}

// ── checkbox group ────────────────────────────────────────────────────────────

function CheckboxGroup({
  options,
  value,
  onChange,
}: {
  options: string[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const toggle = (opt: string) => {
    if (value.includes(opt)) {
      onChange(value.filter((x) => x !== opt));
    } else {
      onChange([...value, opt]);
    }
  };
  return (
    <div className="flex flex-wrap gap-2 mt-1">
      {options.map((opt) => (
        <label key={opt} className="flex items-center gap-1 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={value.includes(opt)}
            onChange={() => toggle(opt)}
            className="rounded"
          />
          {opt}
        </label>
      ))}
    </div>
  );
}

// ── rule form ─────────────────────────────────────────────────────────────────

function RuleForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: FormState;
  onSave: (payload: ReturnType<typeof formToPayload>) => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<FormState>(initial);

  const set = <K extends keyof FormState>(key: K, val: FormState[K]) =>
    setForm((prev) => ({ ...prev, [key]: val }));

  return (
    <div className="bg-gray-800 rounded-lg p-5 space-y-4">
      {/* name + basic */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="sm:col-span-2">
          <label className="block text-xs text-gray-400 mb-1">Rule name *</label>
          <input
            className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="e.g. Prod critical failures"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Priority (lower = first)</label>
          <input
            type="number"
            className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm"
            value={form.priority}
            onChange={(e) => set("priority", Number(e.target.value))}
          />
        </div>
      </div>

      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
            className="rounded"
          />
          Enabled
        </label>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={form.suppress_default}
            onChange={(e) => set("suppress_default", e.target.checked)}
            className="rounded"
          />
          Suppress default recipients when this rule fires
        </label>
      </div>

      {/* conditions */}
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">
          Conditions (AND logic — leave blank for any)
        </h4>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              CR types (comma-separated)
            </label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"
              value={form.match_cr_types_raw}
              onChange={(e) => set("match_cr_types_raw", e.target.value)}
              placeholder="key_rotation, ec2_stop"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Connector types (comma-separated)
            </label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"
              value={form.match_connector_types_raw}
              onChange={(e) => set("match_connector_types_raw", e.target.value)}
              placeholder="aws, azure"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Asset tags (key=value, ...)</label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"
              value={form.match_asset_tags_raw}
              onChange={(e) => set("match_asset_tags_raw", e.target.value)}
              placeholder="env=production, team=security"
            />
          </div>
        </div>
        <div className="mt-3">
          <label className="block text-xs text-gray-400 mb-1">Severity</label>
          <CheckboxGroup
            options={SEVERITY_OPTIONS}
            value={form.match_severities}
            onChange={(v) => set("match_severities", v)}
          />
        </div>
        <div className="mt-3">
          <label className="block text-xs text-gray-400 mb-1">CR status</label>
          <CheckboxGroup
            options={STATUS_OPTIONS}
            value={form.match_status}
            onChange={(v) => set("match_status", v)}
          />
        </div>
      </div>

      {/* actions */}
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">
          Actions
        </h4>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Notify user IDs (comma-separated)
            </label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"
              value={form.notify_user_ids_raw}
              onChange={(e) => set("notify_user_ids_raw", e.target.value)}
              placeholder="uuid1, uuid2"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">
              Channels (comma-separated)
            </label>
            <input
              className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-1.5 text-sm font-mono"
              value={form.notify_channels_raw}
              onChange={(e) => set("notify_channels_raw", e.target.value)}
              placeholder="slack:#ops-alerts, email:sec@co.com"
            />
          </div>
        </div>
        <div className="mt-3">
          <label className="block text-xs text-gray-400 mb-1">Notify roles</label>
          <CheckboxGroup
            options={ROLE_OPTIONS}
            value={form.notify_role_ids}
            onChange={(v) => set("notify_role_ids", v)}
          />
        </div>
      </div>

      <div className="flex gap-3 pt-1">
        <button
          onClick={() => onSave(formToPayload(form))}
          disabled={!form.name.trim()}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium disabled:opacity-50"
        >
          Save rule
        </button>
        <button
          onClick={onCancel}
          className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── preview panel ─────────────────────────────────────────────────────────────

function PreviewPanel({ onClose }: { onClose: () => void }) {
  const [crJson, setCrJson] = useState(
    JSON.stringify(
      {
        change_type: "key_rotation",
        severity: "high",
        status: "failed",
        connector_type: "aws",
        asset_tags: { env: "production" },
      },
      null,
      2
    )
  );
  const [result, setResult] = useState<PreviewResult | null>(null);
  const [error, setError] = useState("");

  const run = async () => {
    setError("");
    setResult(null);
    try {
      const payload = JSON.parse(crJson);
      const res = await apiClient.post("/notification-rules/preview", {
        cr_payload: payload,
      });
      setResult(res.data as PreviewResult);
    } catch (e: unknown) {
      const msg = (e as { message?: string })?.message ?? "Request failed";
      setError(msg);
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Rule preview — paste a CR payload</h3>
        <button onClick={onClose} className="text-gray-400 hover:text-white">
          <X className="w-4 h-4" />
        </button>
      </div>
      <textarea
        className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm font-mono h-48 resize-y"
        value={crJson}
        onChange={(e) => setCrJson(e.target.value)}
      />
      {error && <p className="text-red-400 text-sm">{error}</p>}
      <button
        onClick={run}
        className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 rounded text-sm font-medium"
      >
        Evaluate rules
      </button>
      {result && (
        <div className="space-y-2 text-sm">
          <p className="text-gray-400">
            Matched rules:{" "}
            {result.matched_rules.length ? (
              <span className="text-white">{result.matched_rules.join(", ")}</span>
            ) : (
              <span className="text-gray-500">none</span>
            )}
          </p>
          <p className="text-gray-400">
            Recipients:{" "}
            {result.user_ids.length ? (
              <span className="text-white">{result.user_ids.join(", ")}</span>
            ) : (
              <span className="text-gray-500">none</span>
            )}
          </p>
          {result.channels.length > 0 && (
            <p className="text-gray-400">
              Channels: <span className="text-white">{result.channels.join(", ")}</span>
            </p>
          )}
          {result.suppress_default && (
            <p className="text-yellow-400">Default recipients will be suppressed.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── rule row ──────────────────────────────────────────────────────────────────

function RuleRow({
  rule,
  index,
  total,
  onEdit,
  onDelete,
  onToggle,
  onMoveUp,
  onMoveDown,
}: {
  rule: NotificationRule;
  index: number;
  total: number;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  const conditionSummary = [
    rule.match_cr_types?.length ? `CR: ${rule.match_cr_types.join(", ")}` : null,
    rule.match_severities?.length ? `Severity: ${rule.match_severities.join(", ")}` : null,
    rule.match_status?.length ? `Status: ${rule.match_status.join(", ")}` : null,
    rule.match_connector_types?.length ? `Connector: ${rule.match_connector_types.join(", ")}` : null,
    rule.match_asset_tags
      ? `Tags: ${Object.entries(rule.match_asset_tags)
          .map(([k, v]) => `${k}=${v}`)
          .join(", ")}`
      : null,
  ]
    .filter(Boolean)
    .join(" | ");

  return (
    <div
      className={`flex items-start gap-3 p-4 rounded-lg border ${
        rule.enabled ? "border-gray-700 bg-gray-800" : "border-gray-700/50 bg-gray-800/50 opacity-60"
      }`}
    >
      {/* priority reorder */}
      <div className="flex flex-col items-center gap-0.5 pt-0.5">
        <button
          onClick={onMoveUp}
          disabled={index === 0}
          className="text-gray-500 hover:text-white disabled:opacity-20"
        >
          <ChevronUp className="w-3.5 h-3.5" />
        </button>
        <span className="text-xs text-gray-500 font-mono">{rule.priority}</span>
        <button
          onClick={onMoveDown}
          disabled={index === total - 1}
          className="text-gray-500 hover:text-white disabled:opacity-20"
        >
          <ChevronDown className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm">{rule.name}</span>
          {rule.suppress_default && (
            <span className="text-xs bg-yellow-900/50 text-yellow-300 px-1.5 py-0.5 rounded">
              suppresses default
            </span>
          )}
        </div>
        {conditionSummary && (
          <p className="text-xs text-gray-400 mt-0.5 truncate">{conditionSummary}</p>
        )}
        {!conditionSummary && (
          <p className="text-xs text-gray-500 mt-0.5 italic">Matches all CRs</p>
        )}
        <div className="flex flex-wrap gap-2 mt-1.5 text-xs text-gray-400">
          {rule.notify_user_ids?.length ? (
            <span>{rule.notify_user_ids.length} user(s)</span>
          ) : null}
          {rule.notify_role_ids?.length ? (
            <span>Roles: {rule.notify_role_ids.join(", ")}</span>
          ) : null}
          {rule.notify_channels?.length ? (
            <span>Channels: {rule.notify_channels.join(", ")}</span>
          ) : null}
        </div>
      </div>

      {/* actions */}
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={onToggle}
          title={rule.enabled ? "Disable rule" : "Enable rule"}
          className="text-gray-400 hover:text-white"
        >
          {rule.enabled ? (
            <ToggleRight className="w-5 h-5 text-blue-400" />
          ) : (
            <ToggleLeft className="w-5 h-5" />
          )}
        </button>
        <button
          onClick={onEdit}
          title="Edit"
          className="text-gray-400 hover:text-white"
        >
          <Edit2 className="w-4 h-4" />
        </button>
        <button
          onClick={onDelete}
          title="Delete"
          className="text-gray-400 hover:text-red-400"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ── main page ─────────────────────────────────────────────────────────────────

export function NotificationRules() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  const { data: rules = [], isLoading } = useQuery<NotificationRule[]>({
    queryKey: ["notification-rules"],
    queryFn: () => apiClient.get("/notification-rules").then((r) => r.data),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["notification-rules"] });

  const createMutation = useMutation({
    mutationFn: (payload: ReturnType<typeof formToPayload>) =>
      apiClient.post("/notification-rules", payload),
    onSuccess: () => {
      invalidate();
      setShowForm(false);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ReturnType<typeof formToPayload> }) =>
      apiClient.put(`/notification-rules/${id}`, payload),
    onSuccess: () => {
      invalidate();
      setEditingId(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiClient.delete(`/notification-rules/${id}`),
    onSuccess: invalidate,
  });

  const toggleMutation = useMutation({
    mutationFn: (rule: NotificationRule) =>
      apiClient.put(`/notification-rules/${rule.id}`, {
        ...formToPayload(ruleToForm(rule)),
        enabled: !rule.enabled,
      }),
    onSuccess: invalidate,
  });

  const reorderMutation = useMutation({
    mutationFn: ({
      rule,
      newPriority,
    }: {
      rule: NotificationRule;
      newPriority: number;
    }) =>
      apiClient.put(`/notification-rules/${rule.id}`, {
        ...formToPayload(ruleToForm(rule)),
        priority: newPriority,
      }),
    onSuccess: invalidate,
  });

  const handleMoveUp = (index: number) => {
    const rule = rules[index];
    const prev = rules[index - 1];
    // Swap priorities
    reorderMutation.mutate({ rule, newPriority: prev.priority - 1 });
  };

  const handleMoveDown = (index: number) => {
    const rule = rules[index];
    const next = rules[index + 1];
    reorderMutation.mutate({ rule, newPriority: next.priority + 1 });
  };

  if (isLoading) return <PageLoading />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Notification Rules"
        description="Custom routing rules evaluated in priority order when a change request event fires."
        actions={
          <div className="flex gap-2">
            <button
              onClick={() => setShowPreview((v) => !v)}
              className="flex items-center gap-1.5 px-3 py-2 bg-gray-700 hover:bg-gray-600 rounded text-sm"
            >
              <Eye className="w-4 h-4" />
              Preview
            </button>
            <button
              onClick={() => {
                setEditingId(null);
                setShowForm(true);
              }}
              className="flex items-center gap-1.5 px-3 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium"
            >
              <Plus className="w-4 h-4" />
              Add rule
            </button>
          </div>
        }
      />

      {showPreview && (
        <PreviewPanel onClose={() => setShowPreview(false)} />
      )}

      {showForm && !editingId && (
        <RuleForm
          initial={EMPTY_FORM}
          onSave={(payload) => createMutation.mutate(payload)}
          onCancel={() => setShowForm(false)}
        />
      )}

      {rules.length === 0 && !showForm && (
        <p className="text-gray-500 text-sm">
          No rules yet. Add one to start routing notifications.
        </p>
      )}

      <div className="space-y-2">
        {rules.map((rule, idx) =>
          editingId === rule.id ? (
            <RuleForm
              key={rule.id}
              initial={ruleToForm(rule)}
              onSave={(payload) =>
                updateMutation.mutate({ id: rule.id, payload })
              }
              onCancel={() => setEditingId(null)}
            />
          ) : (
            <RuleRow
              key={rule.id}
              rule={rule}
              index={idx}
              total={rules.length}
              onEdit={() => {
                setShowForm(false);
                setEditingId(rule.id);
              }}
              onDelete={() => deleteMutation.mutate(rule.id)}
              onToggle={() => toggleMutation.mutate(rule)}
              onMoveUp={() => handleMoveUp(idx)}
              onMoveDown={() => handleMoveDown(idx)}
            />
          )
        )}
      </div>
    </div>
  );
}
