import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Key } from "lucide-react";
import { settingsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";

export function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [showKeyInput, setShowKeyInput] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => settingsApi.get(),
  });

  const updateKey = useMutation({
    mutationFn: () => settingsApi.updateAIKey(apiKey),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setShowKeyInput(false);
      setApiKey("");
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    },
  });

  const isAdmin = user?.role === "admin";

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8 max-w-2xl">
      <PageHeader
        title="Settings"
        subtitle="Organization configuration"
      />

      <div className="bg-white border border-slate-200 rounded-lg p-6">
        <div className="flex items-center gap-2 mb-1">
          <Key className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">AI Configuration</h2>
        </div>
        <p className="text-xs text-slate-500 mb-4">
          Anthropic API key for AI-assisted project planning. Key is encrypted at rest and never displayed.
        </p>

        <div className="flex items-center gap-3 mb-3">
          {settings?.ai_configured ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600">
              <Check className="w-4 h-4" />
              Configured
              {settings.updated_at && (
                <span className="text-slate-400 font-normal">
                  · Updated {new Date(settings.updated_at).toLocaleDateString()}
                </span>
              )}
            </span>
          ) : (
            <span className="text-sm text-slate-400">Not configured</span>
          )}

          {isAdmin && !showKeyInput && (
            <button
              onClick={() => setShowKeyInput(true)}
              className="text-sm text-brand-600 hover:underline"
            >
              {settings?.ai_configured ? "Update key" : "Add key"}
            </button>
          )}

          {saved && (
            <span className="text-sm text-emerald-600">✓ Saved</span>
          )}
        </div>

        {isAdmin && showKeyInput && (
          <div className="flex gap-2 items-start">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-ant-api03-..."
              autoFocus
              className="flex-1 text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 font-mono"
            />
            <button
              onClick={() => updateKey.mutate()}
              disabled={!apiKey.trim() || updateKey.isPending}
              className="px-3 py-2 bg-brand-600 text-white text-sm rounded-md hover:bg-brand-700 disabled:opacity-50"
            >
              {updateKey.isPending ? "Saving…" : "Save"}
            </button>
            <button
              onClick={() => { setShowKeyInput(false); setApiKey(""); }}
              className="px-3 py-2 border border-slate-200 text-slate-600 text-sm rounded-md hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
        )}

        {!isAdmin && !settings?.ai_configured && (
          <p className="text-xs text-amber-600">
            AI is not configured. Ask an administrator to add an Anthropic API key.
          </p>
        )}
      </div>
    </div>
  );
}
