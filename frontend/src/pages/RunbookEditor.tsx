import { useState, useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Plus, Trash2, GripVertical, Play } from "lucide-react";
import { useRunbook, useCreateRunbook, useUpdateRunbook, useTriggerRunbook } from "../hooks/useRunbooks";
import type { RunbookStepOut, RunbookExecutionOut } from "../hooks/useRunbooks";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

type StepDraft = Omit<RunbookStepOut, "id" | "runbook_id">;

const BLANK_STEP = (n: number): StepDraft => ({
  step_number: n,
  name: "",
  type: "change",
  on_failure: "abort",
  parallel_steps: [],
});

export function RunbookEditor() {
  const { id } = useParams<{ id: string }>();
  const isNew = !id || id === "new";
  const navigate = useNavigate();

  const { data: existing, isLoading } = useRunbook(isNew ? "" : id!);
  const create = useCreateRunbook();
  const update = useUpdateRunbook(id ?? "");
  const trigger = useTriggerRunbook(id ?? "");

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [steps, setSteps] = useState<StepDraft[]>([BLANK_STEP(1)]);
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [ctxKV, setCtxKV] = useState([{ k: "", v: "" }]);

  useEffect(() => {
    if (existing) {
      setName(existing.name);
      setDescription(existing.description ?? "");
      setTags(existing.tags.join(", "));
      setSteps(
        existing.steps.map((s) => ({
          ...s,
          parallel_steps: s.parallel_steps ?? [],
        }))
      );
    }
  }, [existing]);

  if (!isNew && isLoading) return <PageLoading />;

  const addStep = () =>
    setSteps((prev) => [...prev, BLANK_STEP(prev.length + 1)]);

  const removeStep = (i: number) =>
    setSteps((prev) =>
      prev.filter((_, idx) => idx !== i).map((s, idx) => ({ ...s, step_number: idx + 1 }))
    );

  const updateStep = (i: number, patch: Partial<StepDraft>) =>
    setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, ...patch } : s)));

  const save = async () => {
    const payload = {
      name,
      description: description || undefined,
      tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      steps,
    };
    if (isNew) {
      const rb = await create.mutateAsync(payload);
      navigate(`/runbooks/${rb.id}`);
    } else {
      await update.mutateAsync(payload);
      navigate(`/runbooks/${id}`);
    }
  };

  const handleTrigger = () => {
    const context = Object.fromEntries(
      ctxKV.filter((kv) => kv.k).map((kv) => [kv.k, kv.v])
    );
    trigger.mutate(context, {
      onSuccess: (exec: RunbookExecutionOut) => navigate(`/executions/${exec.id}`),
    });
  };

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <PageHeader
        title={isNew ? "New Runbook" : "Edit Runbook"}
        subtitle={isNew ? "Define steps to compose into a reusable workflow." : `Editing v${existing?.version ?? 1}`}
        actions={
          <div className="flex gap-2">
            {!isNew && (
              <button
                onClick={() => setTriggerOpen(true)}
                className="flex items-center gap-2 px-4 py-2 border border-brand-600 text-brand-600 rounded-md hover:bg-brand-50 text-sm font-medium"
              >
                <Play className="w-4 h-4" /> Trigger
              </button>
            )}
            <button
              onClick={save}
              disabled={create.isPending || update.isPending}
              className="px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium disabled:opacity-50"
            >
              Save
            </button>
          </div>
        }
      />

      <div className="space-y-4 mb-6">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
          <input
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Engineer Onboarding"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Description</label>
          <textarea
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">Tags (comma-separated)</label>
          <input
            className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="e.g. onboarding, identity"
          />
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">Steps</h2>
        <button
          onClick={addStep}
          className="flex items-center gap-1 text-xs text-brand-600 hover:underline"
        >
          <Plus className="w-3 h-3" /> Add step
        </button>
      </div>

      <div className="space-y-3">
        {steps.map((step, i) => (
          <StepCard
            key={i}
            index={i}
            step={step}
            onChange={(patch) => updateStep(i, patch)}
            onRemove={() => removeStep(i)}
          />
        ))}
      </div>

      {/* Trigger modal */}
      {triggerOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-xl w-96 p-6">
            <h3 className="font-semibold text-slate-900 mb-3">Trigger Runbook</h3>
            <p className="text-sm text-slate-500 mb-4">
              Supply runtime context variables (available as <code>ctx</code> in condition expressions).
            </p>
            {ctxKV.map((kv, i) => (
              <div key={i} className="flex gap-2 mb-2">
                <input
                  className="flex-1 border border-slate-300 rounded px-2 py-1 text-sm"
                  placeholder="key"
                  value={kv.k}
                  onChange={(e) =>
                    setCtxKV((prev) => prev.map((x, idx) => idx === i ? { ...x, k: e.target.value } : x))
                  }
                />
                <input
                  className="flex-1 border border-slate-300 rounded px-2 py-1 text-sm"
                  placeholder="value"
                  value={kv.v}
                  onChange={(e) =>
                    setCtxKV((prev) => prev.map((x, idx) => idx === i ? { ...x, v: e.target.value } : x))
                  }
                />
              </div>
            ))}
            <button
              onClick={() => setCtxKV((prev) => [...prev, { k: "", v: "" }])}
              className="text-xs text-brand-600 hover:underline mb-4"
            >
              + Add variable
            </button>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setTriggerOpen(false)}
                className="px-3 py-2 text-sm border border-slate-300 rounded hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                onClick={handleTrigger}
                disabled={trigger.isPending}
                className="px-3 py-2 text-sm bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50"
              >
                {trigger.isPending ? "Starting..." : "Trigger"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StepCard({
  index,
  step,
  onChange,
  onRemove,
}: {
  index: number;
  step: StepDraft;
  onChange: (patch: Partial<StepDraft>) => void;
  onRemove: () => void;
}) {
  return (
    <div className="border border-slate-200 rounded-lg p-4 bg-white">
      <div className="flex items-center gap-2 mb-3">
        <GripVertical className="w-4 h-4 text-slate-300" />
        <span className="text-xs font-medium text-slate-400 w-5">{step.step_number}</span>
        <input
          className="flex-1 border border-slate-300 rounded px-2 py-1 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-brand-500"
          placeholder="Step name"
          value={step.name}
          onChange={(e) => onChange({ name: e.target.value })}
        />
        <select
          className="border border-slate-300 rounded px-2 py-1 text-sm focus:outline-none"
          value={step.type}
          onChange={(e) => onChange({ type: e.target.value })}
        >
          <option value="change">Change</option>
          <option value="condition">Condition</option>
          <option value="human_checkpoint">Human Checkpoint</option>
          <option value="parallel_group">Parallel Group</option>
        </select>
        <button onClick={onRemove} className="text-slate-400 hover:text-red-500">
          <Trash2 className="w-4 h-4" />
        </button>
      </div>

      {step.type === "change" && (
        <div className="space-y-2 pl-7">
          <div>
            <label className="text-xs text-slate-500">Change Type</label>
            <input
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm mt-0.5"
              placeholder="e.g. patch_packages"
              value={step.change_type ?? ""}
              onChange={(e) => onChange({ change_type: e.target.value })}
            />
          </div>
          <div>
            <label className="text-xs text-slate-500">On Failure</label>
            <select
              className="border border-slate-300 rounded px-2 py-1 text-sm ml-2"
              value={step.on_failure}
              onChange={(e) => onChange({ on_failure: e.target.value })}
            >
              <option value="abort">Abort</option>
              <option value="continue">Continue</option>
              <option value="rollback_all">Rollback All</option>
            </select>
          </div>
        </div>
      )}

      {step.type === "condition" && (
        <div className="space-y-2 pl-7">
          <div>
            <label className="text-xs text-slate-500">Expression (Python-safe)</label>
            <input
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm font-mono mt-0.5"
              placeholder="steps[1]['exit_code'] == 0"
              value={step.condition_expr ?? ""}
              onChange={(e) => onChange({ condition_expr: e.target.value })}
            />
          </div>
          <div className="flex gap-4">
            <div>
              <label className="text-xs text-slate-500">If true → step #</label>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-16"
                value={step.on_true_step ?? ""}
                onChange={(e) => onChange({ on_true_step: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">If false → step #</label>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-16"
                value={step.on_false_step ?? ""}
                onChange={(e) => onChange({ on_false_step: Number(e.target.value) })}
              />
            </div>
          </div>
        </div>
      )}

      {step.type === "human_checkpoint" && (
        <div className="space-y-2 pl-7">
          <div>
            <label className="text-xs text-slate-500">Prompt</label>
            <textarea
              className="w-full border border-slate-300 rounded px-2 py-1 text-sm mt-0.5"
              rows={2}
              value={step.prompt ?? ""}
              onChange={(e) => onChange({ prompt: e.target.value })}
            />
          </div>
          <div className="flex gap-4 flex-wrap">
            <div>
              <label className="text-xs text-slate-500">Required Role</label>
              <input
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-28"
                placeholder="admin"
                value={step.required_role ?? ""}
                onChange={(e) => onChange({ required_role: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">Timeout (hours)</label>
              <input
                type="number"
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1 w-16"
                value={step.timeout_hours ?? ""}
                onChange={(e) => onChange({ timeout_hours: Number(e.target.value) || undefined })}
              />
            </div>
            <div>
              <label className="text-xs text-slate-500">On Timeout</label>
              <select
                className="border border-slate-300 rounded px-2 py-1 text-sm ml-1"
                value={step.on_timeout ?? "abort"}
                onChange={(e) => onChange({ on_timeout: e.target.value })}
              >
                <option value="abort">Abort</option>
                <option value="continue">Continue</option>
              </select>
            </div>
          </div>
        </div>
      )}

      {step.type === "parallel_group" && (
        <div className="pl-7 text-xs text-slate-500 italic">
          Parallel group — add child change steps (parallel_steps) via the API for MVP.
        </div>
      )}
    </div>
  );
}
