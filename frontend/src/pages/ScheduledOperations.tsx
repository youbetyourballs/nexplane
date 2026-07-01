// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { formatDistanceToNow, parseISO } from "date-fns";
import {
  Play, Pause, Trash2, Plus, ChevronDown, ChevronRight,
  Clock, CheckCircle2, AlertTriangle, XCircle, RefreshCw,
} from "lucide-react";
import { recurringJobsApi, RecurringJob, RecurringJobCreate } from "../api/endpoints";

const JOB_TYPE_LABELS: Record<string, string> = {
  backup: "Backup",
  scheduled_restore: "Scheduled Restore",
  scheduled_op: "Scheduled Op",
};

const JOB_TYPE_COLORS: Record<string, string> = {
  backup: "bg-blue-100 text-blue-700",
  scheduled_restore: "bg-amber-100 text-amber-700",
  scheduled_op: "bg-slate-100 text-slate-600",
};

function jobStatus(job: RecurringJob): "healthy" | "overdue" | "disabled" | "never_run" {
  if (!job.enabled) return "disabled";
  if (!job.last_run_at) return "never_run";
  return "healthy";
}

function StatusBadge({ job }: { job: RecurringJob }) {
  const status = jobStatus(job);
  const config = {
    healthy: { icon: CheckCircle2, label: "Healthy", cls: "text-emerald-600" },
    overdue: { icon: AlertTriangle, label: "Overdue", cls: "text-amber-600" },
    disabled: { icon: XCircle, label: "Disabled", cls: "text-slate-400" },
    never_run: { icon: Clock, label: "Scheduled", cls: "text-slate-500" },
  }[status];
  const Icon = config.icon;
  return (
    <span className={`flex items-center gap-1 text-xs font-medium ${config.cls}`}>
      <Icon className="w-3.5 h-3.5" />
      {config.label}
    </span>
  );
}

function CronPreview({ cron }: { cron: string }) {
  const labels: Record<string, string> = {
    "0 * * * *": "Hourly",
    "0 2 * * *": "Daily at 2am",
    "0 0 * * *": "Daily at midnight",
    "0 2 * * 1": "Weekly Mon 2am",
    "0 2 * * 0": "Weekly Sun 2am",
  };
  return (
    <span className="text-xs text-slate-400 font-mono">
      {labels[cron] ?? cron}
    </span>
  );
}

function JobRow({ job }: { job: RecurringJob }) {
  const [expanded, setExpanded] = useState(false);
  const qc = useQueryClient();

  const enableMutation = useMutation({
    mutationFn: () => recurringJobsApi.enable(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });
  const disableMutation = useMutation({
    mutationFn: () => recurringJobsApi.disable(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });
  const runNowMutation = useMutation({
    mutationFn: () => recurringJobsApi.runNow(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });
  const deleteMutation = useMutation({
    mutationFn: () => recurringJobsApi.delete(job.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["recurring-jobs"] }),
  });

  return (
    <>
      <tr
        className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${expanded ? "bg-slate-50" : ""}`}
        onClick={() => setExpanded((v) => !v)}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2">
            {expanded ? <ChevronDown className="w-3.5 h-3.5 text-slate-400" /> : <ChevronRight className="w-3.5 h-3.5 text-slate-400" />}
            <span className="text-sm font-medium text-slate-900">{job.name}</span>
          </div>
        </td>
        <td className="px-4 py-3">
          <span className={`px-2 py-0.5 rounded text-xs font-medium ${JOB_TYPE_COLORS[job.job_type]}`}>
            {JOB_TYPE_LABELS[job.job_type]}
          </span>
        </td>
        <td className="px-4 py-3 text-xs text-slate-600">{job.target_description}</td>
        <td className="px-4 py-3"><CronPreview cron={job.cron_expression} /></td>
        <td className="px-4 py-3 text-xs text-slate-500">
          {job.last_run_at ? formatDistanceToNow(parseISO(job.last_run_at), { addSuffix: true }) : "—"}
        </td>
        <td className="px-4 py-3 text-xs text-slate-500">
          {job.next_run_at ? formatDistanceToNow(parseISO(job.next_run_at), { addSuffix: true }) : "—"}
        </td>
        <td className="px-4 py-3"><StatusBadge job={job} /></td>
      </tr>
      {expanded && (
        <tr className="bg-slate-50 border-b border-slate-100">
          <td colSpan={7} className="px-8 py-4">
            <div className="flex items-center gap-3">
              <button
                onClick={(e) => { e.stopPropagation(); runNowMutation.mutate(); }}
                disabled={runNowMutation.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-600 text-white text-xs rounded hover:bg-brand-700 disabled:opacity-50"
              >
                <RefreshCw className="w-3 h-3" />
                Run Now
              </button>
              {job.enabled ? (
                <button
                  onClick={(e) => { e.stopPropagation(); disableMutation.mutate(); }}
                  disabled={disableMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 text-slate-600 text-xs rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  <Pause className="w-3 h-3" />
                  Disable
                </button>
              ) : (
                <button
                  onClick={(e) => { e.stopPropagation(); enableMutation.mutate(); }}
                  disabled={enableMutation.isPending}
                  className="flex items-center gap-1.5 px-3 py-1.5 border border-slate-300 text-slate-600 text-xs rounded hover:bg-slate-100 disabled:opacity-50"
                >
                  <Play className="w-3 h-3" />
                  Enable
                </button>
              )}
              {job.last_cr_id && (
                <Link
                  to={`/change-requests/${job.last_cr_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="text-xs text-brand-600 hover:underline"
                >
                  View last CR →
                </Link>
              )}
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm(`Delete job "${job.name}"? This cannot be undone.`)) {
                    deleteMutation.mutate();
                  }
                }}
                disabled={deleteMutation.isPending}
                className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-red-600 text-xs rounded hover:bg-red-50 disabled:opacity-50"
              >
                <Trash2 className="w-3 h-3" />
                Delete
              </button>
            </div>
            <div className="mt-3 grid grid-cols-3 gap-4 text-xs text-slate-500">
              <div><span className="font-medium text-slate-700">Action:</span> {job.action_id}</div>
              <div><span className="font-medium text-slate-700">Cron:</span> <code>{job.cron_expression}</code></div>
              <div><span className="font-medium text-slate-700">Connector:</span> {job.connector_id ?? "default"}</div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function CreateJobDrawer({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [form, setForm] = useState<RecurringJobCreate>({
    name: "",
    job_type: "scheduled_op",
    action_id: "ssm_command",
    parameters: {},
    target_description: "",
    cron_expression: "0 2 * * *",
    schedule_preset: "daily",
    schedule_hour: 2,
  });
  const [scheduleTab, setScheduleTab] = useState<"daily" | "weekly" | "hourly" | "custom">("daily");
  const [hour, setHour] = useState(2);
  const [customCron, setCustomCron] = useState("");
  const [paramsText, setParamsText] = useState("{}");
  const [paramsError, setParamsError] = useState("");

  const mutation = useMutation({
    mutationFn: (data: RecurringJobCreate) => recurringJobsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["recurring-jobs"] });
      onClose();
    },
  });

  function buildCron(): string {
    if (scheduleTab === "custom") return customCron;
    if (scheduleTab === "hourly") return "0 * * * *";
    if (scheduleTab === "weekly") return `0 ${hour} * * 1`;
    return `0 ${hour} * * *`;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    let params: Record<string, unknown> = {};
    try {
      params = JSON.parse(paramsText);
      setParamsError("");
    } catch {
      setParamsError("Invalid JSON");
      return;
    }
    mutation.mutate({
      ...form,
      cron_expression: buildCron(),
      schedule_preset: scheduleTab === "custom" ? undefined : scheduleTab,
      schedule_hour: scheduleTab === "custom" || scheduleTab === "hourly" ? undefined : hour,
      parameters: params,
    });
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-[480px] bg-white shadow-xl flex flex-col h-full overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">New Scheduled Job</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-sm">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="flex-1 px-6 py-5 space-y-5">
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Name</label>
            <input
              required
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="Daily nexplane DB backup"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Job Type</label>
            <select
              value={form.job_type}
              onChange={(e) => setForm((f) => ({ ...f, job_type: e.target.value as RecurringJobCreate["job_type"] }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="backup">Backup</option>
              <option value="scheduled_restore">Scheduled Restore</option>
              <option value="scheduled_op">Scheduled Op</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Action ID</label>
            <input
              required
              value={form.action_id}
              onChange={(e) => setForm((f) => ({ ...f, action_id: e.target.value }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="ssm_command"
            />
            <p className="text-xs text-slate-400 mt-0.5">Must match a ChangeType value (e.g. ssm_command, create_backup)</p>
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Target Description</label>
            <input
              required
              value={form.target_description}
              onChange={(e) => setForm((f) => ({ ...f, target_description: e.target.value }))}
              className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder="nexplane postgres DB"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-2">Schedule</label>
            <div className="flex gap-1 mb-3">
              {(["daily", "weekly", "hourly", "custom"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  onClick={() => setScheduleTab(tab)}
                  className={`px-3 py-1 text-xs rounded ${scheduleTab === tab ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>
            {scheduleTab !== "hourly" && scheduleTab !== "custom" && (
              <div className="flex items-center gap-2">
                <label className="text-xs text-slate-600">Hour (UTC):</label>
                <input
                  type="number"
                  min={0}
                  max={23}
                  value={hour}
                  onChange={(e) => setHour(Number(e.target.value))}
                  className="w-16 border border-slate-300 rounded px-2 py-1 text-sm text-center"
                />
                <span className="text-xs text-slate-400">→ cron: <code>{buildCron()}</code></span>
              </div>
            )}
            {scheduleTab === "custom" && (
              <div>
                <input
                  value={customCron}
                  onChange={(e) => setCustomCron(e.target.value)}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono"
                  placeholder="0 2 * * 1-5"
                />
                <p className="text-xs text-slate-400 mt-0.5">Standard 5-field cron expression (UTC)</p>
              </div>
            )}
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-700 mb-1">Parameters (JSON)</label>
            <textarea
              value={paramsText}
              onChange={(e) => setParamsText(e.target.value)}
              rows={5}
              className="w-full border border-slate-300 rounded px-3 py-2 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
              placeholder='{"instance_id": "i-abc123", "commands": ["echo hi"]}'
            />
            {paramsError && <p className="text-xs text-red-600 mt-0.5">{paramsError}</p>}
          </div>
          <div className="pt-2 flex gap-3">
            <button
              type="submit"
              disabled={mutation.isPending}
              className="flex-1 bg-brand-600 text-white py-2 rounded text-sm font-medium hover:bg-brand-700 disabled:opacity-50"
            >
              {mutation.isPending ? "Creating..." : "Create Job"}
            </button>
            <button type="button" onClick={onClose} className="px-4 py-2 border border-slate-300 text-slate-600 text-sm rounded hover:bg-slate-50">
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function ScheduledOperations() {
  const [showCreate, setShowCreate] = useState(false);
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ["recurring-jobs"],
    queryFn: () => recurringJobsApi.list(),
    refetchInterval: 30_000,
  });

  const enabled = jobs.filter((j) => j.enabled).length;
  const disabled = jobs.filter((j) => !j.enabled).length;

  if (isLoading) return <div className="p-8 text-sm text-slate-500">Loading...</div>;

  return (
    <div className="p-8 max-w-6xl">
      {showCreate && <CreateJobDrawer onClose={() => setShowCreate(false)} />}

      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Scheduled Operations</h1>
          <p className="text-sm text-slate-500 mt-1">
            {jobs.length} jobs · {enabled} enabled · {disabled} disabled
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700"
        >
          <Plus className="w-4 h-4" />
          New Job
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-lg p-12 text-center">
          <Clock className="w-10 h-10 text-slate-300 mx-auto mb-3" />
          <p className="text-slate-500 text-sm">No scheduled jobs yet.</p>
          <button
            onClick={() => setShowCreate(true)}
            className="mt-4 px-4 py-2 bg-brand-600 text-white text-sm rounded hover:bg-brand-700"
          >
            Create your first job
          </button>
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
          <table className="w-full">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Name</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Type</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Target</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Schedule</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Last Run</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Next Run</th>
                <th className="text-left px-4 py-3 text-xs font-medium text-slate-600">Status</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <JobRow key={job.id} job={job} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default ScheduledOperations;
