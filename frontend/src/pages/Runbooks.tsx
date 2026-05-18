import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Copy, Play, BookOpen, AlertTriangle } from "lucide-react";
import {
  useRunbooks,
  useForkRunbook,
  useTriggerRunbook,
  useToggleRunbookAutoExecute,
} from "../hooks/useRunbooks";
import type { RunbookExecutionOut, RunbookOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";

export function Runbooks() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [search, setSearch] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const { data: runbooks, isLoading } = useRunbooks({
    search: search || undefined,
    tag: tagFilter || undefined,
  });
  const fork = useForkRunbook();

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <PageHeader
        title="Runbooks"
        subtitle="Composable, versioned workflows that chain change requests with conditional logic."
        actions={
          isAdmin ? (
            <button
              onClick={() => navigate("/runbooks/new")}
              className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium"
            >
              <Plus className="w-4 h-4" /> New Runbook
            </button>
          ) : null
        }
      />

      <div className="flex gap-3 mb-6">
        <input
          className="border border-slate-300 rounded-md px-3 py-2 text-sm w-64 focus:outline-none focus:ring-2 focus:ring-brand-500"
          placeholder="Search runbooks..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <input
          className="border border-slate-300 rounded-md px-3 py-2 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-brand-500"
          placeholder="Filter by tag..."
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
        />
      </div>

      {runbooks?.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <BookOpen className="w-12 h-12 mx-auto mb-3 opacity-40" />
          <p className="text-lg font-medium">No runbooks yet</p>
          <p className="text-sm mt-1">Create one or fork a seed template to get started.</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100">
          {runbooks?.map((rb) => (
            <RunbookRow key={rb.id} rb={rb} isAdmin={isAdmin} onFork={() => fork.mutate(rb.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

function RunbookRow({
  rb,
  isAdmin,
  onFork,
}: {
  rb: RunbookOut;
  isAdmin: boolean;
  onFork: () => void;
}) {
  const navigate = useNavigate();
  const toggle = useToggleRunbookAutoExecute(rb.id);

  return (
    <div
      className="flex items-center justify-between px-4 py-3 hover:bg-slate-50 cursor-pointer"
      onClick={() => navigate(`/runbooks/${rb.id}`)}
    >
      <div className="flex items-center gap-3 min-w-0">
        <BookOpen className="w-4 h-4 text-brand-500 flex-shrink-0" />
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-slate-900 truncate">{rb.name}</span>
            {rb.is_seed && (
              <span className="px-1.5 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 rounded">
                Template
              </span>
            )}
            <span className="text-xs text-slate-400">v{rb.version}</span>
          </div>
          <div className="flex gap-1 mt-0.5">
            {rb.tags.map((t) => (
              <span key={t} className="text-xs px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded">
                {t}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3 ml-4 flex-shrink-0">
        {/* Auto-execute toggle — admin only */}
        {isAdmin ? (
          <label
            className="flex items-center gap-1.5 cursor-pointer"
            onClick={(e) => e.stopPropagation()}
            title={rb.auto_execute ? "Disable auto-execution" : "Enable auto-execution"}
          >
            <span className="text-xs text-slate-500">{rb.auto_execute ? "Enabled" : "Disabled"}</span>
            <button
              role="switch"
              aria-checked={rb.auto_execute}
              onClick={(e) => {
                e.stopPropagation();
                toggle.mutate(!rb.auto_execute);
              }}
              disabled={toggle.isPending}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
                rb.auto_execute ? "bg-brand-600" : "bg-slate-300"
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
                  rb.auto_execute ? "translate-x-4" : "translate-x-1"
                }`}
              />
            </button>
          </label>
        ) : (
          <span
            className={`text-xs px-2 py-0.5 rounded font-medium ${
              rb.auto_execute
                ? "bg-green-100 text-green-700"
                : "bg-slate-100 text-slate-500"
            }`}
          >
            {rb.auto_execute ? "Enabled" : "Disabled"}
          </span>
        )}

        {/* Fork / Edit buttons */}
        {rb.is_seed ? (
          <button
            onClick={(e) => { e.stopPropagation(); onFork(); }}
            className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
          >
            <Copy className="w-3 h-3" /> Fork
          </button>
        ) : isAdmin ? (
          <button
            onClick={(e) => { e.stopPropagation(); navigate(`/runbooks/${rb.id}`); }}
            className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
          >
            Edit
          </button>
        ) : null}

        <TriggerButton runbook={rb} />
      </div>
    </div>
  );
}

function TriggerButton({ runbook }: { runbook: RunbookOut }) {
  const navigate = useNavigate();
  const trigger = useTriggerRunbook(runbook.id);
  const [windowWarning, setWindowWarning] = useState<{ name: string; warning: string } | null>(null);

  const handleTrigger = (force = false) => {
    trigger.mutate(
      { context: {}, force },
      {
        onSuccess: (exec: RunbookExecutionOut) => navigate(`/executions/${exec.id}`),
        onError: (err: unknown) => {
          const resp = (err as { response?: { status: number; data?: { maintenance_window?: string; warning?: string } } }).response;
          if (resp?.status === 409 && resp?.data?.maintenance_window) {
            setWindowWarning({
              name: resp.data.maintenance_window,
              warning: resp.data.warning ?? "Active change freeze window detected.",
            });
          }
        },
      }
    );
  };

  return (
    <>
      <button
        onClick={(e) => { e.stopPropagation(); handleTrigger(false); }}
        disabled={!runbook.auto_execute || trigger.isPending}
        title={!runbook.auto_execute ? "Runbook must be enabled by an admin before it can be triggered" : "Run runbook"}
        className="flex items-center gap-1 px-2 py-1 text-xs bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <Play className="w-3 h-3" /> Run
      </button>

      {/* Maintenance window override modal */}
      {windowWarning && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          onClick={() => setWindowWarning(null)}
        >
          <div
            className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 p-6"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-4">
              <AlertTriangle className="w-6 h-6 text-amber-500 flex-shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-slate-900">
                  Change window active: {windowWarning.name}
                </h3>
                <p className="mt-1 text-sm text-slate-600">{windowWarning.warning}</p>
                <p className="mt-2 text-sm font-medium text-slate-700">
                  Only proceed if this is an emergency.
                </p>
              </div>
            </div>
            <div className="flex justify-end gap-3">
              <button
                onClick={() => setWindowWarning(null)}
                className="px-4 py-2 text-sm border border-slate-300 rounded-md hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={() => { setWindowWarning(null); handleTrigger(true); }}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-md hover:bg-red-700"
              >
                Confirm emergency override
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
