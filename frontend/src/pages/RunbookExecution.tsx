// @ts-nocheck — RunbookStepResultOut.result is Record<string,unknown>; JSX unknown propagation pre-dates strict typing
import React from "react";
import { useParams, Link } from "react-router-dom";
import {
  CheckCircle, XCircle, Clock, AlertCircle, Loader2,
} from "lucide-react";
import {
  useRunbookExecution,
  useResumeCheckpoint,
  useAbortExecution,
} from "../hooks/useRunbooks";
import type { RunbookStepResultOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

const STATUS_ICON: Record<string, React.ReactNode> = {
  completed: <CheckCircle className="w-4 h-4 text-green-500" />,
  failed: <XCircle className="w-4 h-4 text-red-500" />,
  running: <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />,
  waiting_human: <AlertCircle className="w-4 h-4 text-amber-500" />,
  pending: <Clock className="w-4 h-4 text-slate-300" />,
  skipped: <Clock className="w-4 h-4 text-slate-300" />,
};

const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  waiting_human: "Awaiting Approval",
  completed: "Completed",
  failed: "Failed",
  rolled_back: "Rolled Back",
};

export function RunbookExecution() {
  const { id } = useParams<{ id: string }>();
  const { data: execution, isLoading } = useRunbookExecution(
    id!,
    /* poll */ true // will always enable polling initially; reactive poll below refines it
  );

  // Reactive poll condition
  const shouldPoll = execution
    ? ["running", "waiting_human"].includes(execution.status)
    : false;

  const { data: polledExecution } = useRunbookExecution(id!, shouldPoll);

  const ex = polledExecution ?? execution;

  const resume = useResumeCheckpoint(id!);
  const abort = useAbortExecution(id!);

  if (isLoading || !ex) return <PageLoading />;

  const runbookName = (ex as unknown as Record<string, unknown> & { runbook_snapshot?: { name?: string } }).runbook_snapshot?.name ?? "Runbook";

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <PageHeader
        title={`Execution: ${runbookName}`}
        subtitle={
          <span className="flex items-center gap-2 text-sm text-slate-500">
            v{ex.runbook_version} ·{" "}
            <span
              className={`font-medium ${
                ex.status === "completed"
                  ? "text-green-600"
                  : ex.status === "failed"
                  ? "text-red-600"
                  : ex.status === "waiting_human"
                  ? "text-amber-600"
                  : "text-blue-600"
              }`}
            >
              {STATUS_LABEL[ex.status] ?? ex.status}
            </span>
          </span>
        }
        actions={
          ["running", "waiting_human"].includes(ex.status) ? (
            <button
              onClick={() => abort.mutate()}
              disabled={abort.isPending}
              className="px-3 py-2 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
            >
              Abort Execution
            </button>
          ) : undefined
        }
      />

      <div className="space-y-3 mt-4">
        {ex.step_results.length === 0 && (
          <p className="text-sm text-slate-500 italic">
            Waiting for first executor tick (up to 30 seconds)…
          </p>
        )}
        {ex.step_results.map((sr) => (
          <StepResultCard
            key={sr.id}
            stepResult={sr}
            executionId={id!}
            onResume={(action) =>
              resume.mutate({ step_number: sr.step_number, action })
            }
            resumePending={resume.isPending}
          />
        ))}
      </div>
    </div>
  );
}

function ConditionResult({ evaluatedTo, jumpedTo }: { evaluatedTo: boolean; jumpedTo: number | null }) {
  return (
    <div className="pl-6 mt-1 text-xs text-slate-500">
      Evaluated to:{" "}
      <span className={evaluatedTo ? "text-green-600" : "text-red-600"}>
        {String(evaluatedTo)}
      </span>
      {jumpedTo !== null ? <> → jumped to step {jumpedTo}</> : null}
    </div>
  );
}

function StepResultCard({
  stepResult: sr,
  executionId: _executionId,
  onResume,
  resumePending,
}: {
  stepResult: RunbookStepResultOut;
  executionId: string;
  onResume: (action: string) => void;
  resumePending: boolean;
}) {
  const duration =
    sr.started_at && sr.completed_at
      ? `${Math.round(
          (new Date(sr.completed_at).getTime() - new Date(sr.started_at).getTime()) / 1000
        )}s`
      : null;


  return (
    <div className="border border-slate-200 rounded-lg p-4 bg-white">
      <div className="flex items-center gap-2 mb-1">
        {STATUS_ICON[sr.status] ?? <Clock className="w-4 h-4 text-slate-300" />}
        <span className="font-medium text-slate-900 text-sm">
          {sr.step_number}. {sr.step_name}
        </span>
        <span className="text-xs px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded ml-auto">
          {sr.step_type}
        </span>
        {duration && <span className="text-xs text-slate-400">{duration}</span>}
      </div>

      {/* Change request links */}
      {sr.change_request_ids.length > 0 && (
        <div className="pl-6 mt-1">
          {sr.change_request_ids.map((crId) => (
            <Link
              key={crId}
              to={`/change-requests/${crId}`}
              className="text-xs text-brand-600 hover:underline block"
            >
              Change Request {crId.slice(0, 8)}…
            </Link>
          ))}
        </div>
      )}

      {/* Condition result */}
      {sr.step_type === "condition" ? (
        <ConditionResult
          evaluatedTo={Boolean(sr.result.evaluated_to)}
          jumpedTo={typeof sr.result.jumped_to_step === "number" ? sr.result.jumped_to_step : null}
        />
      ) : null}

      {/* Human checkpoint prompt + actions */}
      {sr.step_type === "human_checkpoint" && sr.status === "waiting_human" && (
        <div className="pl-6 mt-2 p-3 bg-amber-50 border border-amber-200 rounded-md">
          <p className="text-sm text-amber-900 mb-3">
            {(sr as RunbookStepResultOut & { prompt?: string }).prompt ?? "Manual approval required."}
          </p>
          <div className="flex gap-2">
            <button
              onClick={() => onResume("resume")}
              disabled={resumePending}
              className="px-3 py-1.5 text-sm bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
            >
              Resume
            </button>
            <button
              onClick={() => onResume("abort")}
              disabled={resumePending}
              className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
            >
              Abort
            </button>
          </div>
        </div>
      )}

      {/* Parallel group children */}
      {sr.step_type === "parallel_group" && sr.result?.child_results && (
        <div className="pl-6 mt-1 grid grid-cols-2 gap-1">
          {(sr.result.child_results as { cr_id: string; status: string }[]).map((c) => (
            <Link
              key={c.cr_id}
              to={`/change-requests/${c.cr_id}`}
              className="text-xs text-brand-600 hover:underline"
            >
              {c.cr_id.slice(0, 8)}… ({c.status})
            </Link>
          ))}
        </div>
      )}

      {/* Error */}
      {sr.error_message && (
        <p className="pl-6 mt-1 text-xs text-red-600">{sr.error_message}</p>
      )}
    </div>
  );
}
