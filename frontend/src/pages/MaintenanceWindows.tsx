import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clock, Plus, Trash2, Edit2, ToggleLeft, ToggleRight } from "lucide-react";
import { apiClient } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

interface MaintenanceWindow {
  id: number;
  name: string;
  cron_schedule: string;
  duration_minutes: number;
  applies_to_tags: string[] | null;
  enabled: boolean;
  organization_id: string;
}

interface WindowFormData {
  name: string;
  cron_schedule: string;
  duration_minutes: number;
  applies_to_tags: string;
  enabled: boolean;
}

const EMPTY_FORM: WindowFormData = {
  name: "",
  cron_schedule: "",
  duration_minutes: 60,
  applies_to_tags: "",
  enabled: true,
};

function cronDescription(cron: string): string {
  // Simple human-readable fallback without cronstrue dependency
  const parts = cron.split(" ");
  if (parts.length !== 5) return cron;
  const [min, hour, dom, month, dow] = parts;
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  if (dom === "*" && month === "*" && dow !== "*") {
    const dayName = days[parseInt(dow)] ?? `day ${dow}`;
    return `Every ${dayName} at ${hour.padStart(2, "0")}:${min.padStart(2, "0")} UTC`;
  }
  return cron;
}

export function MaintenanceWindows() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState<WindowFormData>(EMPTY_FORM);
  const [formError, setFormError] = useState("");

  const { data: windows, isLoading } = useQuery<MaintenanceWindow[]>({
    queryKey: ["maintenance-windows"],
    queryFn: () => apiClient.get("/maintenance-windows").then((r) => r.data),
  });

  const createMutation = useMutation({
    mutationFn: (data: Omit<MaintenanceWindow, "id" | "organization_id">) =>
      apiClient.post("/maintenance-windows", data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance-windows"] });
      setShowForm(false);
      setForm(EMPTY_FORM);
      setFormError("");
    },
    onError: (err: any) => {
      setFormError(err.response?.data?.detail ?? "Failed to save maintenance window");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Omit<MaintenanceWindow, "id" | "organization_id"> }) =>
      apiClient.put(`/maintenance-windows/${id}`, data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance-windows"] });
      setShowForm(false);
      setEditingId(null);
      setForm(EMPTY_FORM);
      setFormError("");
    },
    onError: (err: any) => {
      setFormError(err.response?.data?.detail ?? "Failed to update maintenance window");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiClient.delete(`/maintenance-windows/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
  });

  const toggleMutation = useMutation({
    mutationFn: (window: MaintenanceWindow) =>
      apiClient.put(`/maintenance-windows/${window.id}`, {
        ...window,
        enabled: !window.enabled,
        applies_to_tags: window.applies_to_tags,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["maintenance-windows"] }),
  });

  function handleEdit(window: MaintenanceWindow) {
    setEditingId(window.id);
    setForm({
      name: window.name,
      cron_schedule: window.cron_schedule,
      duration_minutes: window.duration_minutes,
      applies_to_tags: (window.applies_to_tags ?? []).join(", "),
      enabled: window.enabled,
    });
    setShowForm(true);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError("");
    const tags = form.applies_to_tags.trim()
      ? form.applies_to_tags.split(",").map((t) => t.trim()).filter(Boolean)
      : null;
    const payload = {
      name: form.name,
      cron_schedule: form.cron_schedule,
      duration_minutes: form.duration_minutes,
      applies_to_tags: tags,
      enabled: form.enabled,
    };
    if (editingId !== null) {
      updateMutation.mutate({ id: editingId, data: payload });
    } else {
      createMutation.mutate(payload);
    }
  }

  if (isLoading) return <PageLoading />;

  return (
    <div className="max-w-4xl mx-auto px-6 py-8 space-y-6">
      <PageHeader
        title="Maintenance Windows"
        description="Schedule time windows when approved change requests are allowed to execute."
        action={
          <button
            onClick={() => { setShowForm(true); setEditingId(null); setForm(EMPTY_FORM); }}
            className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
          >
            <Plus className="w-4 h-4" /> New Window
          </button>
        }
      />

      {showForm && (
        <div className="bg-white border border-slate-200 rounded-lg p-6 space-y-4">
          <h3 className="font-semibold text-slate-800">
            {editingId !== null ? "Edit Maintenance Window" : "New Maintenance Window"}
          </h3>
          {formError && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">
              {formError}
            </div>
          )}
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Name</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  required
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                  placeholder="Weekend Maintenance"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Cron Schedule (UTC)
                </label>
                <input
                  type="text"
                  value={form.cron_schedule}
                  onChange={(e) => setForm({ ...form, cron_schedule: e.target.value })}
                  required
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm font-mono"
                  placeholder="0 2 * * 6"
                />
                {form.cron_schedule && (
                  <p className="text-xs text-slate-500 mt-1">
                    {cronDescription(form.cron_schedule)}
                  </p>
                )}
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Duration (minutes)
                </label>
                <input
                  type="number"
                  min={1}
                  max={10080}
                  value={form.duration_minutes}
                  onChange={(e) => setForm({ ...form, duration_minutes: parseInt(e.target.value) || 60 })}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">
                  Applies to Tags (comma-separated, blank = all)
                </label>
                <input
                  type="text"
                  value={form.applies_to_tags}
                  onChange={(e) => setForm({ ...form, applies_to_tags: e.target.value })}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                  placeholder="prod-web, prod-db"
                />
              </div>
            </div>
            <div className="flex items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
                  className="rounded"
                />
                Enabled
              </label>
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="submit"
                disabled={createMutation.isPending || updateMutation.isPending}
                className="px-4 py-2 bg-blue-600 text-white rounded text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {editingId !== null ? "Save Changes" : "Create Window"}
              </button>
              <button
                type="button"
                onClick={() => { setShowForm(false); setEditingId(null); setFormError(""); }}
                className="px-4 py-2 bg-slate-100 text-slate-700 rounded text-sm hover:bg-slate-200"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {windows?.length === 0 && !showForm && (
        <div className="text-center py-12 text-slate-500">
          <Clock className="w-10 h-10 mx-auto mb-3 text-slate-300" />
          <p className="font-medium">No maintenance windows configured</p>
          <p className="text-sm mt-1">Create a window to control when approved changes execute.</p>
        </div>
      )}

      {(windows?.length ?? 0) > 0 && (
        <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Name</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Schedule</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Duration</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Applies To</th>
                <th className="text-left px-4 py-3 font-medium text-slate-600">Status</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {windows?.map((w) => (
                <tr key={w.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3 font-medium text-slate-800">{w.name}</td>
                  <td className="px-4 py-3 text-slate-600">
                    <span className="font-mono text-xs bg-slate-100 rounded px-1.5 py-0.5">{w.cron_schedule}</span>
                    <span className="ml-2 text-xs text-slate-400">{cronDescription(w.cron_schedule)}</span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{w.duration_minutes} min</td>
                  <td className="px-4 py-3 text-slate-600">
                    {w.applies_to_tags ? w.applies_to_tags.join(", ") : <span className="text-slate-400 italic">All assets</span>}
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => toggleMutation.mutate(w)}
                      title={w.enabled ? "Disable" : "Enable"}
                      className="text-slate-500 hover:text-blue-600"
                    >
                      {w.enabled
                        ? <ToggleRight className="w-5 h-5 text-blue-600" />
                        : <ToggleLeft className="w-5 h-5" />}
                    </button>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2 justify-end">
                      <button
                        onClick={() => handleEdit(w)}
                        className="text-slate-400 hover:text-blue-600"
                        title="Edit"
                      >
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => deleteMutation.mutate(w.id)}
                        className="text-slate-400 hover:text-red-600"
                        title="Delete"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
