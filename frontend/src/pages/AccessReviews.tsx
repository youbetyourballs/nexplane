import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, BookOpen, AlertTriangle } from "lucide-react";
import { reviewCampaignsApi, type CampaignCreate, type CampaignOut } from "../api/reviewCampaigns";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";

const STATUS_COLORS: Record<string, string> = {
  draft: "bg-slate-100 text-slate-700",
  collecting: "bg-yellow-100 text-yellow-800",
  in_review: "bg-blue-100 text-blue-800",
  awaiting_approval: "bg-indigo-100 text-indigo-800",
  completed: "bg-green-100 text-green-800",
  cancelled: "bg-slate-100 text-slate-400",
};

const TYPE_LABELS: Record<string, string> = {
  manager_centric: "Manager",
  resource_owner: "Resource Owner",
  security_team: "Security Team",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${STATUS_COLORS[status] ?? "bg-gray-100 text-gray-700"}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

function isOverdue(campaign: CampaignOut): boolean {
  if (!campaign.due_date || campaign.status === "completed" || campaign.status === "cancelled") return false;
  return new Date(campaign.due_date) < new Date();
}

const EMPTY_FORM: CampaignCreate = {
  title: "",
  description: "",
  campaign_type: "security_team",
  scope: { include_inactive_users: false },
  reviewer_assignment_rule: { type: "security_team", fallback_reviewer_id: null },
  evidence_options: { include_last_login: true, include_days_inactive: true, include_asset_sensitivity: true },
  due_date: null,
};

function CreateWizard({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(1);
  const [form, setForm] = useState<CampaignCreate>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const qc = useQueryClient();

  const createMutation = useMutation({
    mutationFn: (data: CampaignCreate) => reviewCampaignsApi.create(data),
    onSuccess: (campaign) => {
      qc.invalidateQueries({ queryKey: ["review-campaigns"] });
      onClose();
      navigate(`/access-reviews/${campaign.id}`);
    },
    onError: (e: any) => setError(e.response?.data?.detail ?? "Failed to create campaign"),
  });

  const update = (patch: Partial<CampaignCreate>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-xl mx-4 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="font-semibold text-slate-900">New Access Review Campaign</h2>
          <span className="text-xs text-slate-400">Step {step} of 4</span>
        </div>
        <div className="px-6 py-5 space-y-4">
          {error && <p className="text-sm text-red-600 bg-red-50 rounded p-2">{error}</p>}
          {step === 1 && (
            <>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Campaign Title *</label>
                <input
                  value={form.title}
                  onChange={(e) => update({ title: e.target.value })}
                  placeholder="Q2 2026 SOC 1 User Access Review"
                  className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-2">Campaign Type *</label>
                {(["manager_centric", "resource_owner", "security_team"] as const).map((t) => (
                  <label key={t} className="flex items-start gap-3 mb-2 cursor-pointer">
                    <input type="radio" name="campaign_type" value={t} checked={form.campaign_type === t}
                      onChange={() => update({ campaign_type: t, reviewer_assignment_rule: { type: t, fallback_reviewer_id: form.reviewer_assignment_rule.fallback_reviewer_id } })}
                      className="mt-0.5" />
                    <span className="text-sm">
                      <span className="font-medium">{TYPE_LABELS[t]}</span>
                      <span className="text-slate-500 ml-1">
                        {t === "manager_centric" && "— managers certify their team's access"}
                        {t === "resource_owner" && "— system owners certify who can access their systems"}
                        {t === "security_team" && "— security team reviews everything"}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Due Date</label>
                <input type="date"
                  value={form.due_date?.substring(0, 10) ?? ""}
                  onChange={(e) => update({ due_date: e.target.value ? e.target.value + "T00:00:00Z" : null })}
                  className="border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
              </div>
            </>
          )}
          {step === 2 && (
            <>
              <p className="text-sm text-slate-600">Scope controls which connectors and users are included. Leave blank to include all.</p>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">Asset Tags (comma-separated)</label>
                <input value={(form.scope.asset_tags ?? []).join(", ")}
                  onChange={(e) => update({ scope: { ...form.scope, asset_tags: e.target.value ? e.target.value.split(",").map((t) => t.trim()) : null } })}
                  placeholder="pci-in-scope, sox-relevant"
                  className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-sm font-medium text-slate-700 mb-1">User Groups (comma-separated)</label>
                <input value={(form.scope.user_groups ?? []).join(", ")}
                  onChange={(e) => update({ scope: { ...form.scope, user_groups: e.target.value ? e.target.value.split(",").map((t) => t.trim()) : null } })}
                  placeholder="Engineering, Finance"
                  className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm" />
              </div>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" checked={form.scope.include_inactive_users ?? false}
                  onChange={(e) => update({ scope: { ...form.scope, include_inactive_users: e.target.checked } })} />
                Include suspended / disabled users
              </label>
            </>
          )}
          {step === 3 && (
            <>
              <p className="text-sm text-slate-600">Choose which evidence fields to collect for each entry.</p>
              {[
                { key: "include_last_login" as const, label: "Last login date" },
                { key: "include_days_inactive" as const, label: "Days since last login" },
                { key: "include_asset_sensitivity" as const, label: "Asset criticality and tags" },
              ].map(({ key, label }) => (
                <label key={key} className="flex items-center gap-2 text-sm text-slate-700">
                  <input type="checkbox" checked={form.evidence_options[key]}
                    onChange={(e) => update({ evidence_options: { ...form.evidence_options, [key]: e.target.checked } })} />
                  {label}
                </label>
              ))}
            </>
          )}
          {step === 4 && (
            <div className="space-y-2 text-sm">
              <p className="font-medium text-slate-900">Review and Launch</p>
              <div className="bg-slate-50 rounded-lg p-4 space-y-1 text-slate-700">
                <p><span className="font-medium">Title:</span> {form.title}</p>
                <p><span className="font-medium">Type:</span> {TYPE_LABELS[form.campaign_type]}</p>
                <p><span className="font-medium">Asset tags:</span> {form.scope.asset_tags?.join(", ") || "All"}</p>
                <p><span className="font-medium">User groups:</span> {form.scope.user_groups?.join(", ") || "All"}</p>
                <p><span className="font-medium">Due:</span> {form.due_date ? new Date(form.due_date).toLocaleDateString() : "No deadline"}</p>
              </div>
              <p className="text-slate-500 text-xs">Launching will collect access entries from all connected identity systems in scope.</p>
            </div>
          )}
        </div>
        <div className="px-6 py-4 border-t border-slate-200 flex justify-between">
          <button onClick={() => step === 1 ? onClose() : setStep((s) => s - 1)}
            className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900">
            {step === 1 ? "Cancel" : "Back"}
          </button>
          {step < 4 ? (
            <button onClick={() => setStep((s) => s + 1)} disabled={step === 1 && !form.title.trim()}
              className="px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50">
              Next
            </button>
          ) : (
            <button onClick={() => createMutation.mutate(form)} disabled={createMutation.isPending}
              className="px-4 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50">
              {createMutation.isPending ? "Creating..." : "Launch Campaign"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function AccessReviews() {
  const [showWizard, setShowWizard] = useState(false);
  const [tab, setTab] = useState<"all" | "mine">("all");
  const navigate = useNavigate();

  const { data: campaigns, isLoading } = useQuery({
    queryKey: ["review-campaigns", tab],
    queryFn: () => reviewCampaignsApi.list(tab === "mine" ? { mine: true } : {}),
    refetchInterval: 5000,
  });

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-6 max-w-5xl mx-auto">
      {showWizard && <CreateWizard onClose={() => setShowWizard(false)} />}
      <PageHeader
        title="Access Reviews"
        subtitle="Campaign-based access certification for SOC 1, SOC 2, and separation-of-duties reviews."
        actions={
          <button onClick={() => setShowWizard(true)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 text-sm font-medium">
            <Plus className="w-4 h-4" /> New Campaign
          </button>
        }
      />
      <div className="flex gap-1 border-b border-slate-200 mb-6">
        {(["all", "mine"] as const).map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t ? "border-brand-600 text-brand-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
            {t === "all" ? "All Campaigns" : "My Reviews"}
          </button>
        ))}
      </div>
      {campaigns?.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <BookOpen className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="font-medium">No campaigns yet</p>
          <p className="text-sm mt-1">Create a campaign to start reviewing access across your connected systems.</p>
        </div>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 divide-y divide-slate-100">
          {campaigns?.map((c) => (
            <div key={c.id} onClick={() => navigate(`/access-reviews/${c.id}`)}
              className="flex items-center justify-between px-5 py-4 hover:bg-slate-50 cursor-pointer">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium text-slate-900 truncate">{c.title}</span>
                  <StatusBadge status={c.status} />
                  <span className="text-xs text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">
                    {TYPE_LABELS[c.campaign_type] ?? c.campaign_type}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1 text-xs text-slate-400">
                  <span>Created {new Date(c.created_at).toLocaleDateString()}</span>
                  {c.due_date && (
                    <span className={isOverdue(c) ? "text-red-500 font-medium flex items-center gap-1" : ""}>
                      {isOverdue(c) && <AlertTriangle className="w-3 h-3" />}
                      Due {new Date(c.due_date).toLocaleDateString()}
                    </span>
                  )}
                  {c.error_message && <span className="text-red-500">Error: {c.error_message.substring(0, 60)}</span>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
