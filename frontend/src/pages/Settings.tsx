import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Key, Terminal, Copy } from "lucide-react";
import { settingsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { useAuth } from "../hooks/useAuth";
import type { AIProviders } from "../types/api";

export function Settings() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const [showKeyInput, setShowKeyInput] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [saved, setSaved] = useState(false);
  const [generatedSecret, setGeneratedSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [editingProvider, setEditingProvider] = useState<string | null>(null);
  const [apiKeyInput, setApiKeyInput] = useState("");

  const token = localStorage.getItem("nexplane_token") ?? "";

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => settingsApi.get(),
  });

  const { data: aiProviders, refetch: refetchAIProviders } = useQuery<AIProviders>({
    queryKey: ["ai-providers"],
    queryFn: () =>
      fetch("/settings/ai-providers", { headers: { Authorization: `Bearer ${token}` } }).then((r) => r.json()),
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

  const generateSecret = useMutation({
    mutationFn: () => settingsApi.generateAgentSecret(),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["settings"] });
      setGeneratedSecret(data.agent_secret_plaintext ?? null);
    },
  });

  const setProviderMutation = useMutation({
    mutationFn: ({ provider, key }: { provider: string; key: string }) =>
      fetch(`/settings/ai-providers/${provider}`, {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: key }),
      }).then((r) => { if (!r.ok) throw new Error("Failed"); return r.json(); }),
    onSuccess: () => {
      refetchAIProviders();
      setEditingProvider(null);
      setApiKeyInput("");
    },
  });

  const setDefaultMutation = useMutation({
    mutationFn: (provider: string) =>
      fetch("/settings/ai-providers/default", {
        method: "PUT",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ provider }),
      }),
    onSuccess: () => refetchAIProviders(),
  });

  const isAdmin = user?.role === "admin";

  function copySecret() {
    if (generatedSecret) {
      navigator.clipboard.writeText(generatedSecret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8 max-w-2xl">
      <PageHeader title="Settings" subtitle="Organization configuration" />

      {/* AI Providers */}
      <div className="bg-white rounded-lg border border-gray-200 p-6 mb-4">
        <h3 className="text-sm font-semibold text-gray-900 mb-1">AI Providers</h3>
        <p className="text-xs text-gray-500 mb-4">Configure API keys for AI planning assistance. Select the default provider.</p>
        <div className="space-y-3">
          {(["anthropic", "openai"] as const).map((provider) => {
            const info = aiProviders?.providers?.[provider];
            const isDefault = aiProviders?.default === provider;
            const isEditing = editingProvider === provider;
            return (
              <div key={provider} className="flex items-start gap-3 py-2 border-b border-gray-50 last:border-0">
                <input
                  type="radio"
                  name="default-provider"
                  checked={isDefault}
                  disabled={!info?.configured}
                  onChange={() => setDefaultMutation.mutate(provider)}
                  className="mt-1 accent-indigo-600"
                  title="Set as default"
                />
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium capitalize">
                      {provider === "anthropic" ? "Anthropic (Claude)" : "OpenAI"}
                    </span>
                    {isDefault && (
                      <span className="text-xs bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full">Default</span>
                    )}
                    <span className={`text-xs ${info?.configured ? "text-green-600" : "text-gray-400"}`}>
                      {info?.configured ? "● Configured" : "○ Not configured"}
                    </span>
                  </div>
                  {isEditing && (
                    <div className="flex gap-2 mt-2">
                      <input
                        type="password"
                        value={apiKeyInput}
                        onChange={(e) => setApiKeyInput(e.target.value)}
                        placeholder={provider === "anthropic" ? "sk-ant-..." : "sk-..."}
                        className="flex-1 border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-indigo-500"
                        autoFocus
                      />
                      <button
                        onClick={() => setProviderMutation.mutate({ provider, key: apiKeyInput })}
                        disabled={setProviderMutation.isPending || !apiKeyInput}
                        className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 disabled:opacity-50"
                      >
                        Save
                      </button>
                      <button onClick={() => setEditingProvider(null)} className="text-xs text-gray-500">
                        Cancel
                      </button>
                    </div>
                  )}
                </div>
                {!isEditing && (
                  <button
                    onClick={() => { setEditingProvider(provider); setApiKeyInput(""); }}
                    className="text-xs text-indigo-600 hover:underline mt-0.5"
                  >
                    {info?.configured ? "Update" : "Add key"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Legacy AI Configuration (Anthropic only) */}
      <div className="bg-white border border-slate-200 rounded-lg p-6 mb-4">
        <div className="flex items-center gap-2 mb-1">
          <Key className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">AI Configuration (Legacy)</h2>
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
            <button onClick={() => setShowKeyInput(true)} className="text-sm text-brand-600 hover:underline">
              {settings?.ai_configured ? "Update key" : "Add key"}
            </button>
          )}
          {saved && <span className="text-sm text-emerald-600">✓ Saved</span>}
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

      {/* Agent Configuration */}
      <div className="bg-white border border-slate-200 rounded-lg p-6">
        <div className="flex items-center gap-2 mb-1">
          <Terminal className="w-4 h-4 text-slate-400" />
          <h2 className="text-sm font-semibold text-slate-900">Agent Configuration</h2>
        </div>
        <p className="text-xs text-slate-500 mb-4">
          Shared secret used by Nexplane agents to authenticate with the control plane.
          The secret is shown only once when generated — store it securely.
        </p>
        <div className="flex items-center gap-3 mb-3">
          {settings?.agent_configured ? (
            <span className="inline-flex items-center gap-1.5 text-sm text-emerald-600">
              <Check className="w-4 h-4" />
              Configured
            </span>
          ) : (
            <span className="text-sm text-slate-400">Not configured</span>
          )}
          {isAdmin && (
            <button
              onClick={() => { setGeneratedSecret(null); generateSecret.mutate(); }}
              disabled={generateSecret.isPending}
              className="text-sm text-brand-600 hover:underline disabled:opacity-50"
            >
              {generateSecret.isPending
                ? "Generating…"
                : settings?.agent_configured
                ? "Rotate secret"
                : "Generate secret"}
            </button>
          )}
        </div>

        {generatedSecret && (
          <div className="bg-slate-50 border border-slate-200 rounded-md p-3">
            <p className="text-xs text-amber-600 mb-2 font-medium">
              ⚠ Copy this secret now — it will not be shown again.
            </p>
            <div className="flex items-center gap-2">
              <code className="flex-1 text-xs font-mono text-slate-800 break-all">{generatedSecret}</code>
              <button
                onClick={copySecret}
                className="shrink-0 p-1.5 text-slate-400 hover:text-slate-600 rounded border border-slate-200 hover:bg-white"
                title="Copy to clipboard"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
            </div>
            <p className="text-xs text-slate-400 mt-2">
              Pass to the agent: <code className="font-mono">--secret {generatedSecret}</code>
            </p>
          </div>
        )}

        {!isAdmin && !settings?.agent_configured && (
          <p className="text-xs text-amber-600">
            Agent is not configured. Ask an administrator to generate an agent secret.
          </p>
        )}
      </div>
    </div>
  );
}
