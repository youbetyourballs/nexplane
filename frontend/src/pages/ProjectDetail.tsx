import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Plus, X, ChevronUp, ChevronDown, Search,
} from "lucide-react";
import { projectsApi } from "../api/endpoints";
import { changeRequestsApi } from "../api/endpoints";
import { assetsApi } from "../api/endpoints";
import { PageLoading } from "../components/LoadingSpinner";
import { StatusBadge } from "../components/StatusBadge";
import type {
  ProjectDetail, ProjectMember, ProjectStatus, ChangeType,
} from "../types/api";

const STATUS_OPTIONS: ProjectStatus[] = ["draft", "in_progress", "completed", "cancelled"];

const CHANGE_TYPES: ChangeType[] = [
  "dns_update", "snapshot_asset", "security_group_update",
  "key_rotation", "telemetry_agent_deploy", "remote_command", "microsegmentation_policy",
];

function crActionLabel(member: ProjectMember, isDraft: boolean): string {
  if (isDraft) return "View →";
  const s = member.change_request.status;
  if (s === "draft") return "Generate Plan";
  if (s === "planned") return "Submit for Approval";
  if (s === "awaiting_approval") return "View Approvals";
  if (s === "approved" && member.eligible) return "Execute";
  if (["executing", "verifying"].includes(s)) return "Executing…";
  if (s === "completed") return "Completed ✓";
  return "—";
}

function crActionDisabled(member: ProjectMember, isDraft: boolean): boolean {
  if (isDraft) return false;
  const s = member.change_request.status;
  return (
    ["executing", "verifying", "completed", "rolled_back", "failed"].includes(s) ||
    (s === "approved" && !member.eligible)
  );
}

export function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const isNew = id === "new";
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [status, setStatus] = useState<ProjectStatus>("draft");
  const [headerDirty, setHeaderDirty] = useState(false);

  const [showAddExisting, setShowAddExisting] = useState(false);
  const [addSearch, setAddSearch] = useState("");
  const [showNewCrForm, setShowNewCrForm] = useState(false);
  const [depsPopoverFor, setDepsPopoverFor] = useState<string | null>(null);

  const [newCrTitle, setNewCrTitle] = useState("");
  const [newCrType, setNewCrType] = useState<ChangeType>("dns_update");
  const [newCrAssets, setNewCrAssets] = useState<string[]>([]);
  const [newCrOutcome, setNewCrOutcome] = useState("{}");
  const [newCrJsonError, setNewCrJsonError] = useState("");

  const { data: project, isLoading } = useQuery({
    queryKey: ["project", id],
    queryFn: () => projectsApi.get(id!),
    enabled: !isNew && !!id,
    refetchInterval: (data) => {
      if (!data) return false;
      const active = data.members.some((m) =>
        ["executing", "verifying"].includes(m.change_request.status)
      );
      return active ? 5000 : false;
    },
  });

  const { data: allCRs } = useQuery({
    queryKey: ["change-requests"],
    queryFn: () => changeRequestsApi.list(),
    enabled: !isNew,
  });

  const { data: assets } = useQuery({
    queryKey: ["assets"],
    queryFn: () => assetsApi.list(),
    enabled: showNewCrForm,
  });

  useEffect(() => {
    if (project) {
      setName(project.name);
      setGoal(project.goal);
      setStatus(project.status);
      setHeaderDirty(false);
    }
  }, [project?.id]);

  const isDraft = !isNew && project?.status === "draft";
  const isInProgress = project?.status === "in_progress";

  const createProject = useMutation({
    mutationFn: () => projectsApi.create({ name: name.trim(), goal: goal.trim() }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      navigate(`/projects/${p.id}`, { replace: true });
    },
  });

  const updateProject = useMutation({
    mutationFn: () => projectsApi.update(id!, { name: name.trim(), goal: goal.trim(), status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project", id] });
      setHeaderDirty(false);
    },
  });

  const addMember = useMutation({
    mutationFn: (crId: string) =>
      projectsApi.addMember(id!, {
        change_request_id: crId,
        sequence_order: project?.members.length ?? 0,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", id] });
      setShowAddExisting(false);
      setAddSearch("");
    },
  });

  const removeMember = useMutation({
    mutationFn: (pcrId: string) => projectsApi.removeMember(id!, pcrId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const reorderMember = useMutation({
    mutationFn: ({ pcrId, order }: { pcrId: string; order: number }) =>
      projectsApi.updateMember(id!, pcrId, { sequence_order: order }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const setDeps = useMutation({
    mutationFn: ({ pcrId, deps }: { pcrId: string; deps: string[] }) =>
      projectsApi.updateMember(id!, pcrId, { depends_on: deps }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", id] });
      setDepsPopoverFor(null);
    },
  });

  const createAndAddCr = useMutation({
    mutationFn: async () => {
      let outcome: Record<string, unknown>;
      try {
        outcome = JSON.parse(newCrOutcome);
      } catch {
        setNewCrJsonError("Invalid JSON");
        throw new Error("Invalid JSON");
      }
      const cr = await changeRequestsApi.create({
        title: newCrTitle.trim(),
        change_type: newCrType,
        target_asset_ids: newCrAssets,
        desired_outcome: outcome,
      });
      await projectsApi.addMember(id!, {
        change_request_id: cr.id,
        sequence_order: project?.members.length ?? 0,
      });
      return cr;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", id] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
      setShowNewCrForm(false);
      setNewCrTitle("");
      setNewCrOutcome("{}");
      setNewCrJsonError("");
    },
  });

  const executeCr = useMutation({
    mutationFn: (crId: string) => changeRequestsApi.execute(crId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const submitCr = useMutation({
    mutationFn: (crId: string) => changeRequestsApi.submitForApproval(crId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["project", id] }),
  });

  const existingCrIds = new Set(project?.members.map((m) => m.change_request_id) ?? []);
  const availableCRs = (allCRs ?? []).filter(
    (cr) =>
      !existingCrIds.has(cr.id) &&
      (addSearch === "" || cr.title.toLowerCase().includes(addSearch.toLowerCase()))
  );

  if (!isNew && isLoading) return <PageLoading />;

  const members = project?.members ?? [];

  function handleSave() {
    if (isNew) {
      if (name.trim()) createProject.mutate();
    } else {
      updateProject.mutate();
    }
  }

  function handleMoveUp(member: ProjectMember, idx: number) {
    if (idx === 0) return;
    const prev = members[idx - 1];
    reorderMember.mutate({ pcrId: member.id, order: prev.sequence_order });
    reorderMember.mutate({ pcrId: prev.id, order: member.sequence_order });
  }

  function handleMoveDown(member: ProjectMember, idx: number) {
    if (idx === members.length - 1) return;
    const next = members[idx + 1];
    reorderMember.mutate({ pcrId: member.id, order: next.sequence_order });
    reorderMember.mutate({ pcrId: next.id, order: member.sequence_order });
  }

  function handleCrAction(member: ProjectMember) {
    const s = member.change_request.status;
    if (isDraft || s === "draft") {
      navigate(`/change-requests/${member.change_request_id}`);
    } else if (s === "planned") {
      submitCr.mutate(member.change_request_id);
    } else if (s === "awaiting_approval") {
      navigate(`/change-requests/${member.change_request_id}`);
    } else if (s === "approved" && member.eligible) {
      executeCr.mutate(member.change_request_id);
    } else {
      navigate(`/change-requests/${member.change_request_id}`);
    }
  }

  const completedCount = members.filter((m) => m.change_request.status === "completed").length;
  const executingCount = members.filter((m) =>
    ["executing", "verifying"].includes(m.change_request.status)
  ).length;
  const blockedCount = members.filter(
    (m) => !m.eligible && !["completed", "executing", "verifying"].includes(m.change_request.status)
  ).length;
  const pendingCount = members.length - completedCount - executingCount - blockedCount;

  return (
    <div className="p-8 max-w-4xl">
      {/* Header */}
      <div className="flex items-start gap-3 mb-6">
        <button
          onClick={() => navigate("/projects")}
          className="mt-1 p-1.5 text-slate-400 hover:text-slate-600 rounded hover:bg-slate-100 shrink-0"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
        <div className="flex-1 min-w-0">
          <input
            value={name}
            onChange={(e) => { setName(e.target.value); setHeaderDirty(true); }}
            placeholder="Project name…"
            className="w-full text-xl font-semibold text-slate-900 bg-transparent border-b border-transparent hover:border-slate-200 focus:border-brand-400 focus:outline-none pb-0.5 mb-1"
          />
          <input
            value={goal}
            onChange={(e) => { setGoal(e.target.value); setHeaderDirty(true); }}
            placeholder="Describe the goal of this project…"
            className="w-full text-sm text-slate-500 bg-transparent border-b border-transparent hover:border-slate-200 focus:border-brand-400 focus:outline-none pb-0.5"
          />
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!isNew && (
            <select
              value={status}
              onChange={(e) => { setStatus(e.target.value as ProjectStatus); setHeaderDirty(true); }}
              className="text-sm border border-slate-200 rounded-md px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s.replace(/_/g, " ")}</option>
              ))}
            </select>
          )}
          <button
            onClick={handleSave}
            disabled={!name.trim() || (!headerDirty && !isNew) || createProject.isPending || updateProject.isPending}
            className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-40"
          >
            {isNew
              ? (createProject.isPending ? "Creating…" : "Create Project")
              : (updateProject.isPending ? "Saving…" : "Save")}
          </button>
        </div>
      </div>

      {!isNew && (
        <>
          {/* Status summary bar */}
          {isInProgress && members.length > 0 && (
            <div className="flex gap-4 mb-4 text-sm">
              <span className="text-emerald-600">● {completedCount} completed</span>
              <span className="text-brand-600">⟳ {executingCount} executing</span>
              <span className="text-amber-500">⏳ {blockedCount} blocked</span>
              <span className="text-slate-400">○ {pendingCount} pending</span>
            </div>
          )}

          {/* Member list */}
          <div className="bg-white border border-slate-200 rounded-lg divide-y divide-slate-100">
            {members.length === 0 && (
              <div className="p-8 text-center text-slate-400 text-sm">
                No change requests yet. Add existing ones or create new ones below.
              </div>
            )}

            {members.map((member, idx) => (
              <div key={member.id} className="px-4 py-3">
                <div className="flex items-center gap-3">
                  {isDraft && (
                    <div className="flex flex-col gap-0.5 shrink-0">
                      <button
                        onClick={() => handleMoveUp(member, idx)}
                        disabled={idx === 0}
                        className="text-slate-300 hover:text-slate-500 disabled:opacity-20"
                      >
                        <ChevronUp className="w-3.5 h-3.5" />
                      </button>
                      <button
                        onClick={() => handleMoveDown(member, idx)}
                        disabled={idx === members.length - 1}
                        className="text-slate-300 hover:text-slate-500 disabled:opacity-20"
                      >
                        <ChevronDown className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  )}

                  <span className="text-xs text-slate-400 w-5 shrink-0 text-right">{idx + 1}</span>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-slate-900 truncate">
                        {member.change_request.title}
                      </span>
                      <span className="text-xs text-slate-400 shrink-0">
                        [{member.change_request.change_type.replace(/_/g, " ")}]
                      </span>
                      <StatusBadge status={member.change_request.status} size="sm" />
                    </div>
                    <div className="text-xs mt-0.5">
                      {member.depends_on.length === 0 ? (
                        <span className="text-slate-300">Depends on: —</span>
                      ) : member.eligible ? (
                        <span className="text-emerald-500">✓ Dependencies met</span>
                      ) : (
                        <span className="text-amber-500">
                          ⏳ Waiting on:{" "}
                          {member.depends_on
                            .map((depId) => {
                              const dep = members.find((m) => m.id === depId);
                              return dep ? dep.change_request.title : depId.slice(0, 8);
                            })
                            .join(", ")}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 shrink-0">
                    {(isDraft || isInProgress) && (
                      <div className="relative">
                        <button
                          onClick={() =>
                            setDepsPopoverFor(depsPopoverFor === member.id ? null : member.id)
                          }
                          className="text-xs text-slate-400 hover:text-slate-600 px-2 py-1 border border-slate-200 rounded hover:bg-slate-50"
                        >
                          Set deps ▾
                        </button>
                        {depsPopoverFor === member.id && (
                          <div className="absolute right-0 top-8 z-20 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-64">
                            <p className="text-xs text-slate-500 mb-2">This CR depends on:</p>
                            <div className="space-y-1 max-h-48 overflow-y-auto">
                              {members
                                .filter((m) => m.id !== member.id)
                                .map((other) => {
                                  const checked = member.depends_on.includes(other.id);
                                  return (
                                    <label
                                      key={other.id}
                                      className="flex items-center gap-2 text-xs cursor-pointer hover:bg-slate-50 p-1 rounded"
                                    >
                                      <input
                                        type="checkbox"
                                        checked={checked}
                                        onChange={() => {
                                          const next = checked
                                            ? member.depends_on.filter((d) => d !== other.id)
                                            : [...member.depends_on, other.id];
                                          setDeps.mutate({ pcrId: member.id, deps: next });
                                        }}
                                        className="rounded border-slate-300 text-brand-600"
                                      />
                                      <span className="truncate">{other.change_request.title}</span>
                                    </label>
                                  );
                                })}
                            </div>
                            <button
                              onClick={() => setDepsPopoverFor(null)}
                              className="mt-2 text-xs text-slate-400 hover:text-slate-600"
                            >
                              Close
                            </button>
                          </div>
                        )}
                      </div>
                    )}

                    <button
                      onClick={() => handleCrAction(member)}
                      disabled={crActionDisabled(member, isDraft ?? false)}
                      className={`text-xs px-2.5 py-1 rounded font-medium transition-colors ${
                        member.change_request.status === "approved" && member.eligible && !isDraft
                          ? "bg-brand-600 text-white hover:bg-brand-700"
                          : member.change_request.status === "completed"
                          ? "text-emerald-600 bg-emerald-50 cursor-default"
                          : "text-slate-600 border border-slate-200 hover:bg-slate-50 disabled:opacity-40"
                      }`}
                    >
                      {crActionLabel(member, isDraft ?? false)}
                    </button>

                    {isDraft && (
                      <button
                        onClick={() => removeMember.mutate(member.id)}
                        className="text-slate-300 hover:text-red-400 p-1"
                      >
                        <X className="w-4 h-4" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}

            {isDraft && (
              <div className="px-4 py-3 flex gap-2 bg-slate-50">
                <div className="relative">
                  <button
                    onClick={() => { setShowAddExisting(!showAddExisting); setShowNewCrForm(false); }}
                    className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-md hover:bg-white"
                  >
                    <Search className="w-3.5 h-3.5" />
                    Add existing CR
                  </button>
                  {showAddExisting && (
                    <div className="absolute left-0 top-9 z-20 bg-white border border-slate-200 rounded-lg shadow-lg w-80">
                      <div className="p-2 border-b border-slate-100">
                        <input
                          autoFocus
                          value={addSearch}
                          onChange={(e) => setAddSearch(e.target.value)}
                          placeholder="Search change requests…"
                          className="w-full text-sm px-2 py-1 focus:outline-none"
                        />
                      </div>
                      <div className="max-h-56 overflow-y-auto">
                        {availableCRs.length === 0 && (
                          <div className="p-3 text-xs text-slate-400 text-center">No results</div>
                        )}
                        {availableCRs.map((cr) => (
                          <button
                            key={cr.id}
                            onClick={() => addMember.mutate(cr.id)}
                            className="w-full text-left px-3 py-2 hover:bg-slate-50 text-sm"
                          >
                            <div className="font-medium text-slate-900 truncate">{cr.title}</div>
                            <div className="text-xs text-slate-400">
                              {cr.change_type.replace(/_/g, " ")} · {cr.status}
                            </div>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <button
                  onClick={() => { setShowNewCrForm(!showNewCrForm); setShowAddExisting(false); }}
                  className="inline-flex items-center gap-1.5 text-sm text-slate-600 hover:text-slate-900 px-3 py-1.5 border border-slate-200 rounded-md hover:bg-white"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Create new CR
                </button>
              </div>
            )}
          </div>

          {showNewCrForm && isDraft && (
            <div className="mt-3 bg-white border border-slate-200 rounded-lg p-5">
              <h3 className="text-sm font-semibold text-slate-900 mb-4">Create & Add Change Request</h3>
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div className="col-span-2">
                  <label className="block text-xs text-slate-500 mb-1">Title</label>
                  <input
                    value={newCrTitle}
                    onChange={(e) => setNewCrTitle(e.target.value)}
                    placeholder="e.g. Update firewall policy for payments subnet"
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">Change Type</label>
                  <select
                    value={newCrType}
                    onChange={(e) => setNewCrType(e.target.value as ChangeType)}
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5"
                  >
                    {CHANGE_TYPES.map((t) => (
                      <option key={t} value={t}>{t.replace(/_/g, " ")}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-slate-500 mb-1">
                    Target Assets ({newCrAssets.length} selected)
                  </label>
                  <select
                    multiple
                    value={newCrAssets}
                    onChange={(e) =>
                      setNewCrAssets(Array.from(e.target.selectedOptions, (o) => o.value))
                    }
                    className="w-full text-sm border border-slate-200 rounded px-3 py-1.5 h-20"
                  >
                    {(assets ?? []).map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name} ({a.environment})
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="block text-xs text-slate-500 mb-1">Desired Outcome (JSON)</label>
                  <textarea
                    value={newCrOutcome}
                    onChange={(e) => { setNewCrOutcome(e.target.value); setNewCrJsonError(""); }}
                    rows={4}
                    className={`w-full text-xs font-mono border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 ${
                      newCrJsonError ? "border-red-400" : "border-slate-200"
                    }`}
                  />
                  {newCrJsonError && <p className="text-xs text-red-500 mt-1">{newCrJsonError}</p>}
                </div>
              </div>
              <div className="flex gap-2">
                <button
                  onClick={() => createAndAddCr.mutate()}
                  disabled={!newCrTitle.trim() || createAndAddCr.isPending}
                  className="px-3 py-1.5 bg-brand-600 text-white text-sm rounded hover:bg-brand-700 disabled:opacity-50"
                >
                  {createAndAddCr.isPending ? "Creating…" : "Create & Add to Project"}
                </button>
                <button
                  onClick={() => { setShowNewCrForm(false); setNewCrTitle(""); setNewCrOutcome("{}"); }}
                  className="px-3 py-1.5 border border-slate-200 text-slate-600 text-sm rounded hover:bg-slate-50"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
