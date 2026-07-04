// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight, ShieldAlert, Layers, RotateCcw, CheckCircle2,
  AlertTriangle, Play, FileText, Clock, Terminal, ChevronDown, XCircle,
} from "lucide-react";

const IAC_CHANGE_TYPES = new Set(["terraform_apply", "ansible_playbook", "helm_upgrade"]);

function PlanOutputPanel({ title, output }: { title: string; output: string }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <button
        className="flex items-center gap-2 w-full px-5 py-3.5 border-b border-slate-100 bg-slate-50 text-left"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <Terminal className="w-4 h-4 text-slate-500" />
        <h3 className="text-sm font-semibold text-slate-700 flex-1">{title}</h3>
        <ChevronDown
          className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? "" : "-rotate-90"}`}
        />
      </button>
      {expanded && (
        <div className="p-5">
          <pre className="text-xs font-mono bg-slate-950 text-slate-100 rounded p-4 overflow-auto max-h-96 whitespace-pre-wrap">
            {output.split("\n").map((line, i) => {
              const cls = line.startsWith("+")
                ? "text-green-400"
                : line.startsWith("-")
                ? "text-red-400"
                : "text-slate-100";
              return (
                <span key={i} className={cls}>
                  {line}
                  {"\n"}
                </span>
              );
            })}
          </pre>
        </div>
      )}
    </div>
  );
}
import { AutoMigrationStepper } from "../components/AutoMigrationStepper";
import { CrLogTail } from "../components/CrLogTail";
import { changeRequestsApi, backupApi, BackupContext } from "../api/endpoints";
import { apiClient } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { RiskBadge } from "../components/RiskBadge";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";
import { format, formatDistanceToNow, parseISO } from "date-fns";

function Section({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      <div className="flex items-center gap-2 px-5 py-3.5 border-b border-slate-100 bg-slate-50">
        <Icon className="w-4 h-4 text-slate-500" />
        <h3 className="text-sm font-semibold text-slate-700">{title}</h3>
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function JsonViewer({ data }: { data: unknown }) {
  return (
    <pre className="text-xs font-mono bg-slate-50 rounded p-3 overflow-auto max-h-48 text-slate-700 border border-slate-100">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}

function BatchProgress({ stepMetadata }: { stepMetadata: any }) {
  if (!stepMetadata?.batches?.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Batch</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Hosts</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Status</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Failures</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {stepMetadata.batches.map((batch: any) => {
            const failures = Object.values(batch.results || {}).filter((r: any) => r.error || !r.running).length;
            return (
              <tr key={batch.batch_index} className={batch.status === "aborted" ? "bg-red-50" : ""}>
                <td className="px-3 py-2 text-slate-700">#{batch.batch_index + 1}</td>
                <td className="px-3 py-2 text-slate-600">{batch.asset_ids?.length ?? 0}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    batch.status === "completed" ? "bg-green-100 text-green-700" :
                    batch.status === "aborted" ? "bg-red-100 text-red-700" :
                    batch.status === "running" ? "bg-blue-100 text-blue-700" :
                    "bg-slate-100 text-slate-600"
                  }`}>
                    {batch.status}
                  </span>
                </td>
                <td className="px-3 py-2 text-slate-600">{failures}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {stepMetadata.aborted && (
        <p className="text-sm text-red-600 mt-2 px-3">
          Rollout aborted: {stepMetadata.failure_count} of {stepMetadata.total_dispatched} hosts failed.
        </p>
      )}
    </div>
  );
}

function FleetHealthCheckResult({ stepMetadata }: { stepMetadata: any }) {
  if (!stepMetadata?.per_host) return null;
  const entries = Object.entries(stepMetadata.per_host) as [string, any][];
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-50 border-b border-slate-200">
          <tr>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Host ID</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Disk OK</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Load OK</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">No Reboot</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Services</th>
            <th className="text-left px-3 py-2 font-medium text-slate-600">Pass</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {entries.map(([assetId, r]) => (
            <tr key={assetId} className={!r.pass ? "bg-red-50" : ""}>
              <td className="px-3 py-2 font-mono text-xs text-slate-700">{assetId}</td>
              <td className="px-3 py-2">{r.disk_free_ok ? "✓" : "✗"}</td>
              <td className="px-3 py-2">{r.load_ok ? "✓" : "✗"}</td>
              <td className="px-3 py-2">{r.no_pending_reboot ? "✓" : "✗"}</td>
              <td className="px-3 py-2">
                {r.services && Object.keys(r.services).length > 0
                  ? Object.entries(r.services).map(([svc, ok]: [string, any]) => (
                      <span key={svc} className={`inline-block mr-1 px-1.5 py-0.5 rounded text-xs ${ok ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"}`}>
                        {svc}
                      </span>
                    ))
                  : <span className="text-slate-400 text-xs">—</span>}
              </td>
              <td className="px-3 py-2">
                <span className={`font-medium ${r.pass ? "text-green-600" : "text-red-600"}`}>
                  {r.pass ? "Pass" : "Fail"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const BANNER_STATUSES = new Set(["planned", "awaiting_approval", "approved"]);

function BackupConfidenceBanner({ crId, status }: { crId: string; status: string }) {
  const { data: ctx } = useQuery<BackupContext>({
    queryKey: ["backup-context", crId],
    queryFn: () => backupApi.getBackupContext(crId),
    enabled: BANNER_STATUSES.has(status),
    retry: false,
  });

  if (!ctx || (!ctx.has_backup && !ctx.overdue)) return null;

  if (!ctx.has_backup) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm">
        <XCircle className="w-4 h-4 text-red-500 flex-shrink-0 mt-0.5" />
        <div>
          <span className="font-medium text-red-800">No backup found for this target</span>
          <span className="text-red-700"> — proceed with caution</span>
        </div>
      </div>
    );
  }

  if (ctx.overdue) {
    return (
      <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm">
        <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0 mt-0.5" />
        <div className="flex-1">
          <span className="font-medium text-amber-800">Last backup is overdue</span>
          {ctx.last_successful_at && (
            <span className="text-amber-700"> — {formatDistanceToNow(parseISO(ctx.last_successful_at), { addSuffix: true })}</span>
          )}
          <span className="text-amber-700"> — consider running a fresh backup before proceeding</span>
        </div>
        {ctx.backup_cr_id && (
          <Link to={`/change-requests/${ctx.backup_cr_id}`} className="text-xs text-amber-700 hover:underline flex-shrink-0">
            View backup CR →
          </Link>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm">
      <CheckCircle2 className="w-4 h-4 text-emerald-500 flex-shrink-0 mt-0.5" />
      <div className="flex-1">
        <span className="font-medium text-emerald-800">Target has a recent backup</span>
        {ctx.last_successful_at && (
          <span className="text-emerald-700"> from {formatDistanceToNow(parseISO(ctx.last_successful_at), { addSuffix: true })}</span>
        )}
      </div>
      {ctx.backup_cr_id && (
        <Link to={`/change-requests/${ctx.backup_cr_id}`} className="text-xs text-emerald-700 hover:underline flex-shrink-0">
          View backup CR →
        </Link>
      )}
    </div>
  );
}

export function ChangeRequestDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [approvalComment, setApprovalComment] = useState("");
  const [actionError, setActionError] = useState("");

  const ACTIVE_STATUSES = new Set([
    "executing", "verifying", "pending", "preflight_running",
    "batch_running", "queued_for_maintenance",
  ]);
  const { data: cr, isLoading } = useQuery({
    queryKey: ["change-request", id],
    queryFn: () => changeRequestsApi.get(id!),
    refetchInterval: (query) =>
      query.state.data?.status && ACTIVE_STATUSES.has(query.state.data.status) ? 2000 : false,
  });

  const { data: auditEvents } = useQuery({
    queryKey: ["change-request-audit", id],
    queryFn: () => changeRequestsApi.getAuditEvents(id!),
    refetchInterval: 5000,
  });

  const isFleetType = cr && ["rolling_restart", "canary_config_push", "distribute_file", "fleet_health_check"].includes(cr.change_type);
  const isFleetActive = cr && ["batch_running", "preflight_running", "queued_for_maintenance"].includes(cr.status);

  const { data: progress } = useQuery({
    queryKey: ["change-request-progress", id],
    queryFn: () => changeRequestsApi.getProgress(id!),
    enabled: !!isFleetType,
    refetchInterval: isFleetActive ? 5000 : false,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["change-request", id] });
    qc.invalidateQueries({ queryKey: ["change-request-audit", id] });
    qc.invalidateQueries({ queryKey: ["change-requests"] });
  };

  const planMutation = useMutation({
    mutationFn: () => changeRequestsApi.generatePlan(id!),
    onSuccess: invalidate,
    onError: (e: any) => setActionError(e.response?.data?.detail?.message || e.message),
  });

  const submitMutation = useMutation({
    mutationFn: () => changeRequestsApi.submitForApproval(id!),
    onSuccess: invalidate,
  });

  const approveMutation = useMutation({
    mutationFn: () => changeRequestsApi.approve(id!, { decision: "approved", comment: approvalComment }),
    onSuccess: () => { invalidate(); setApprovalComment(""); },
  });

  const rejectMutation = useMutation({
    mutationFn: () => changeRequestsApi.reject(id!, { decision: "rejected", comment: approvalComment }),
    onSuccess: () => { invalidate(); setApprovalComment(""); },
  });

  const executeMutation = useMutation({
    mutationFn: () => changeRequestsApi.execute(id!),
    onSuccess: () => {
      invalidate();
      // Force rapid polling for 30s after execute so status updates appear immediately
      const interval = setInterval(() => {
        qc.invalidateQueries({ queryKey: ["change-request", id] });
        qc.invalidateQueries({ queryKey: ["change-request-audit", id] });
      }, 1500);
      setTimeout(() => clearInterval(interval), 30000);
    },
    onError: (e: any) => setActionError(e.response?.data?.detail || e.message),
  });

  const cancelMutation = useMutation({
    mutationFn: () => changeRequestsApi.cancel(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["change-request", id] }),
    onError: (e: any) => setActionError(e.response?.data?.detail ?? "Failed to cancel change request"),
  });

  const rollbackMutation = useMutation({
    mutationFn: () => changeRequestsApi.rollback(id!),
    onSuccess: invalidate,
  });

  const confirmStatefulMutation = useMutation({
    mutationFn: () =>
      apiClient.post(`/change-requests/${cr?.id}/confirm-stateful`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["change-request", id] }),
  });

  if (isLoading || !cr) return <PageLoading />;

  const canApprove = user?.role === "approver" || user?.role === "admin";
  const canExecute = user?.role === "admin" || user?.role === "security_operator";
  const canRollback = user?.role === "admin" || user?.role === "approver";

  return (
    <div className="p-8 max-w-5xl">
      <div className="flex items-center gap-2 text-xs text-slate-400 mb-4">
        <button onClick={() => navigate("/change-requests")} className="hover:text-slate-700">
          Change Requests
        </button>
        <ChevronRight className="w-3 h-3" />
        <span className="text-slate-600 truncate">{cr.title}</span>
      </div>

      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{cr.title}</h1>
          <div className="flex items-center gap-2 mt-2">
            <StatusBadge status={cr.status} />
            <RiskBadge level={cr.risk_level} />
            <span className="text-xs text-slate-400">
              {cr.change_type.replace(/_/g, " ")}
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-2 flex-shrink-0">
          {cr.status === "draft" && (
            <button
              onClick={() => { setActionError(""); planMutation.mutate(); }}
              disabled={planMutation.isPending}
              className="px-3 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50"
            >
              {planMutation.isPending ? "Generating..." : "Generate Change Plan"}
            </button>
          )}
          {cr.status === "planned" && (
            <button
              onClick={() => submitMutation.mutate()}
              disabled={submitMutation.isPending}
              className="px-3 py-2 bg-amber-600 text-white text-sm rounded-md hover:bg-amber-700 disabled:opacity-50"
            >
              Submit for Approval
            </button>
          )}
          {cr.status === "approved" && canExecute && (
            <button
              onClick={() => { setActionError(""); executeMutation.mutate(); }}
              disabled={executeMutation.isPending}
              className="px-3 py-2 bg-emerald-600 text-white text-sm rounded-md hover:bg-emerald-700 disabled:opacity-50 flex items-center gap-1.5"
            >
              <Play className="w-3.5 h-3.5" />
              {executeMutation.isPending ? "Initiating..." : "Execute Change"}
            </button>
          )}
          {["completed", "failed"].includes(cr.status) && canRollback && (
            <button
              onClick={() => rollbackMutation.mutate()}
              disabled={rollbackMutation.isPending}
              className="px-3 py-2 bg-orange-600 text-white text-sm rounded-md hover:bg-orange-700 disabled:opacity-50 flex items-center gap-1.5"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Manual Rollback
            </button>
          )}
          {["draft", "planned", "awaiting_approval", "approved", "queued_for_maintenance"].includes(cr.status) && (
            <button
              onClick={() => {
                if (window.confirm("Cancel this change request? This cannot be undone.")) {
                  setActionError("");
                  cancelMutation.mutate();
                }
              }}
              disabled={cancelMutation.isPending}
              className="px-3 py-2 border border-slate-300 text-slate-600 text-sm rounded-md hover:bg-red-50 hover:border-red-300 hover:text-red-700 disabled:opacity-50 transition-colors"
            >
              {cancelMutation.isPending ? "Cancelling..." : "Cancel Request"}
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-md text-sm text-red-700">
          {actionError}
        </div>
      )}

      {cr.status === "queued_for_maintenance" && (
        <div className="mb-4 bg-yellow-50 border border-yellow-200 rounded-lg px-5 py-4 text-sm text-yellow-800">
          <strong>Queued for Maintenance Window</strong> — This change is approved but no maintenance window is currently open. It will execute automatically when a scheduled window opens.
        </div>
      )}

      <div className="grid grid-cols-3 gap-4 mb-6 text-sm">
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs text-slate-400 mb-1">Requester</div>
          <div className="font-medium text-slate-900">{cr.requester.name}</div>
          <div className="text-slate-500 text-xs">{cr.requester.email}</div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs text-slate-400 mb-1">Created</div>
          <div className="font-medium text-slate-900">
            {format(new Date(cr.created_at), "MMM d, yyyy HH:mm")}
          </div>
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-4">
          <div className="text-xs text-slate-400 mb-1">Last Updated</div>
          <div className="font-medium text-slate-900">
            {format(new Date(cr.updated_at), "MMM d, yyyy HH:mm")}
          </div>
        </div>
      </div>

      {cr.description && (
        <div className="bg-white border border-slate-200 rounded-lg p-5 mb-4 text-sm text-slate-700">
          {cr.description}
        </div>
      )}

      <div className="mb-4">
        <BackupConfidenceBanner crId={cr.id} status={cr.status} />
      </div>

      <div className="space-y-4">
        {/* IaC Plan Output Panel */}
        {IAC_CHANGE_TYPES.has(cr.change_type) &&
          cr.change_plan?.blast_radius?.impact_description && (
            <PlanOutputPanel
              title={cr.change_plan.blast_radius.panel_title ?? "Plan Output"}
              output={cr.change_plan.blast_radius.impact_description}
            />
          )}

        {cr.change_plan && (
          <>
            <Section title="Blast Radius" icon={ShieldAlert}>
              <div className="grid grid-cols-2 gap-4 mb-4 text-sm">
                <div>
                  <div className="text-xs text-slate-400 mb-1">Estimated Impact</div>
                  <div className="text-slate-700">{cr.change_plan.blast_radius.estimated_impact}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-400 mb-1">Recovery Time</div>
                  <div className="text-slate-700">{cr.change_plan.blast_radius.recovery_time_estimate}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-400 mb-1">Affected Environments</div>
                  <div className="flex gap-1">
                    {cr.change_plan.blast_radius.affected_environments.map((e) => (
                      <span key={e} className="px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-xs">{e}</span>
                    ))}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-slate-400 mb-1">Rollback Available</div>
                  <div className={`text-sm font-medium ${cr.change_plan.blast_radius.rollback_available ? "text-emerald-600" : "text-red-600"}`}>
                    {cr.change_plan.blast_radius.rollback_available ? "Yes" : "No — Manual Required"}
                  </div>
                </div>
              </div>
              {cr.change_plan.blast_radius.affected_services.length > 0 && (
                <div>
                  <div className="text-xs text-slate-400 mb-1.5">Affected Services</div>
                  <div className="flex flex-wrap gap-1">
                    {cr.change_plan.blast_radius.affected_services.map((s) => (
                      <span key={s} className="px-2 py-0.5 bg-orange-50 text-orange-700 border border-orange-200 rounded text-xs">{s}</span>
                    ))}
                  </div>
                </div>
              )}
            </Section>

            <Section title="Execution Steps" icon={Layers}>
              <div className="space-y-2">
                {cr.change_plan.generated_steps.map((step) => (
                  <div key={step.step_number} className="flex gap-3 p-3 bg-slate-50 rounded border border-slate-100">
                    <div className="w-6 h-6 rounded-full bg-brand-100 text-brand-700 text-xs font-bold flex items-center justify-center flex-shrink-0 mt-0.5">
                      {step.step_number}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-slate-900">{step.name}</div>
                      <div className="text-xs text-slate-400 mt-0.5">
                        {step.connector_action} · ~{step.estimated_duration_seconds}s
                      </div>
                      {step.rollback_action && (
                        <div className="text-xs text-orange-600 mt-0.5">↩ Rollback: {step.rollback_action}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Safety Checks" icon={CheckCircle2}>
              <div className="space-y-2">
                {cr.change_plan.preflight_checks.map((check) => (
                  <div key={check.name} className="flex items-start gap-3 p-3 border border-slate-100 rounded">
                    <CheckCircle2 className="w-4 h-4 text-slate-300 mt-0.5 flex-shrink-0" />
                    <div>
                      <div className="text-sm font-medium text-slate-800">{check.name.replace(/_/g, " ")}</div>
                      <div className="text-xs text-slate-400">{check.description}</div>
                      <div className="text-xs text-emerald-600 mt-0.5">Expected: {check.expected_result}</div>
                    </div>
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Rollback Plan" icon={RotateCcw}>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <div className="text-xs text-slate-400 mb-1">Strategy</div>
                  <div className="font-medium text-slate-900">
                    {String(cr.change_plan.rollback_plan.strategy).replace(/_/g, " ")}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-slate-400 mb-1">Automatic</div>
                  <div className={`font-medium ${cr.change_plan.rollback_plan.automatic ? "text-emerald-600" : "text-amber-600"}`}>
                    {cr.change_plan.rollback_plan.automatic ? "Yes" : "Manual"}
                  </div>
                </div>
              </div>
              <div className="mt-3 text-sm text-slate-600">
                {String(cr.change_plan.rollback_plan.description)}
              </div>
            </Section>
          </>
        )}

        {isFleetType && progress?.step_metadata && (
          <Section
            title={cr.change_type === "fleet_health_check" ? "Health Check Results" : "Batch Execution Progress"}
            icon={Layers}
          >
            {cr.change_type === "fleet_health_check"
              ? <FleetHealthCheckResult stepMetadata={progress.step_metadata} />
              : <BatchProgress stepMetadata={progress.step_metadata} />}
          </Section>
        )}

        {cr.approvals && cr.approvals.length > 0 && (
          <Section title="Approval History" icon={CheckCircle2}>
            <div className="space-y-3">
              {cr.approvals.map((approval) => (
                <div key={approval.id} className="flex items-start gap-3 p-3 border border-slate-100 rounded">
                  <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${approval.decision === "approved" ? "bg-emerald-500" : "bg-red-500"}`} />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-slate-900">{approval.approver.name}</span>
                      <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${approval.decision === "approved" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                        {approval.decision === "approved" ? "Approved" : "Rejected"}
                      </span>
                      <span className="text-xs text-slate-400">{format(new Date(approval.created_at), "MMM d, HH:mm")}</span>
                    </div>
                    {approval.comment && (
                      <div className="text-sm text-slate-600 mt-1">{approval.comment}</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Section>
        )}

        {cr.status === "awaiting_approval" && canApprove && (
          <Section title="Approval Decision" icon={CheckCircle2}>
            <div className="space-y-3">
              <textarea
                value={approvalComment}
                onChange={(e) => setApprovalComment(e.target.value)}
                placeholder="Add a comment (optional)"
                rows={3}
                className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
              />
              <div className="flex gap-2">
                <button
                  onClick={() => approveMutation.mutate()}
                  disabled={approveMutation.isPending}
                  className="px-4 py-2 bg-emerald-600 text-white text-sm rounded-md hover:bg-emerald-700 disabled:opacity-50"
                >
                  Approve
                </button>
                <button
                  onClick={() => rejectMutation.mutate()}
                  disabled={rejectMutation.isPending}
                  className="px-4 py-2 bg-red-600 text-white text-sm rounded-md hover:bg-red-700 disabled:opacity-50"
                >
                  Reject
                </button>
              </div>
            </div>
          </Section>
        )}

        {cr.execution_runs && cr.execution_runs.length > 0 && (
          <Section title="Execution Run" icon={Play}>
            {cr.execution_runs.slice(-1).map((run) => (
              <div key={run.id} className="space-y-3">
                <div className="grid grid-cols-3 gap-4 text-sm">
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Workflow ID</div>
                    <div className="font-mono text-xs text-slate-700">{run.workflow_id}</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Status</div>
                    <StatusBadge status={run.status} size="sm" />
                  </div>
                  <div>
                    <div className="text-xs text-slate-400 mb-1">Started</div>
                    <div className="text-slate-700">{format(new Date(run.started_at), "HH:mm:ss")}</div>
                  </div>
                </div>
                {Object.keys(run.result).length > 0 && (
                  <div>
                    <div className="text-xs text-slate-400 mb-1.5">Result</div>
                    <JsonViewer data={run.result} />
                  </div>
                )}
              </div>
            ))}
          </Section>
        )}

        {cr.change_type === "agent_containerize_auto" && (() => {
          const execRun = (cr.execution_runs ?? [])[0];
          const stepResults = (execRun?.result as Record<string, unknown> | undefined)?.step_results as Record<string, Record<string, unknown>> | undefined;
          const statefulGate = stepResults?.stateful_gate as Record<string, unknown> | undefined;
          const needsStatefulConfirm = statefulGate?.status === "waiting";

          return (
            <>
              <AutoMigrationStepper stepResults={stepResults} crStatus={cr.status} />

              {needsStatefulConfirm && (
                <div className="mt-4 p-4 bg-amber-50 border border-amber-200 rounded-lg">
                  <h3 className="text-sm font-semibold text-amber-800 mb-1 flex items-center gap-2">
                    ⚠ Stateful workloads detected — human review required
                  </h3>
                  <p className="text-xs text-amber-700 mb-3">
                    The AI identified stateful workloads. Expand the AI Analysis stage above to review the classification and data risk, then confirm to proceed to build.
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => confirmStatefulMutation.mutate()}
                      disabled={confirmStatefulMutation.isPending}
                      className="px-4 py-2 bg-amber-600 text-white text-sm font-medium rounded-md hover:bg-amber-700 disabled:opacity-50"
                    >
                      {confirmStatefulMutation.isPending ? "Confirming…" : "Confirm — proceed with migration"}
                    </button>
                  </div>
                </div>
              )}
            </>
          );
        })()}

        {(cr.status === "executing" || cr.status === "rolling_back") && (
          <div>
            <div className="flex items-center gap-2 px-5 py-3.5 border border-slate-200 border-b-0 rounded-t-lg bg-slate-50">
              <Terminal className="w-4 h-4 text-slate-500" />
              <h3 className="text-sm font-semibold text-slate-700">Live Execution Log</h3>
            </div>
            <CrLogTail
              crId={cr.id}
              onDone={() => {
                invalidate();
              }}
            />
          </div>
        )}

        {auditEvents && auditEvents.length > 0 && (
          <Section title="Audit Trail" icon={FileText}>
            <div className="space-y-1">
              {auditEvents.map((event) => (
                <div key={event.id} className="flex items-start gap-3 py-2 border-b border-slate-50 last:border-0">
                  <div className="text-xs font-mono text-slate-400 flex-shrink-0 pt-0.5">
                    {format(new Date(event.created_at), "HH:mm:ss")}
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-medium text-brand-700 font-mono">{event.event_type}</span>
                    {Object.keys(event.event_payload).length > 0 && (
                      <span className="text-xs text-slate-400 ml-2">
                        {JSON.stringify(event.event_payload).slice(0, 80)}
                        {JSON.stringify(event.event_payload).length > 80 && "..."}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}
