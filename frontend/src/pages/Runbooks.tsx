import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus, Copy, Play, BookOpen } from "lucide-react";
import { useRunbooks, useForkRunbook, useTriggerRunbook } from "../hooks/useRunbooks";
import type { RunbookExecutionOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

export function Runbooks() {
  const navigate = useNavigate();
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
          <button
            onClick={() => navigate("/runbooks/new")}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium"
          >
            <Plus className="w-4 h-4" /> New Runbook
          </button>
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
            <div
              key={rb.id}
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
              <div className="flex items-center gap-2 ml-4 flex-shrink-0">
                {rb.is_seed ? (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      fork.mutate(rb.id);
                    }}
                    className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
                  >
                    <Copy className="w-3 h-3" /> Fork
                  </button>
                ) : (
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/runbooks/${rb.id}`);
                    }}
                    className="flex items-center gap-1 px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-100"
                  >
                    Edit
                  </button>
                )}
                <TriggerButton runbookId={rb.id} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TriggerButton({ runbookId }: { runbookId: string }) {
  const navigate = useNavigate();
  const trigger = useTriggerRunbook(runbookId);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        trigger.mutate(
          {},
          { onSuccess: (exec: RunbookExecutionOut) => navigate(`/executions/${exec.id}`) }
        );
      }}
      disabled={trigger.isPending}
      className="flex items-center gap-1 px-2 py-1 text-xs bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50"
    >
      <Play className="w-3 h-3" /> Run
    </button>
  );
}
