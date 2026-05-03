import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Key, Terminal, Copy } from "lucide-react";
import { settingsApi } from "../api/endpoints";
import { apiClient } from "../api/client";
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
  const [modelInput, setModelInput] = useState("");
  const [agentPlatform, setAgentPlatform] = useState<"linux" | "linux-arm64" | "windows">("linux");
  const [copiedCmd, setCopiedCmd] = useState<string | null>(null);

  const { data: settings, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: () => settingsApi.get(),
  });

  const { data: aiProviders, refetch: refetchAIProviders } = useQuery<AIProviders>({
    queryKey: ["ai-providers"],
    queryFn: () => apiClient.get("/settings/ai-providers").then((r) => r.data),
  });

  const { data: agentVersion } = useQuery<string | null>({
    queryKey: ["agent-version"],
    queryFn: () =>
      apiClient
        .get<string>("/downloads/version", { responseType: "text" })
        .then((r) => (typeof r.data === "string" ? r.data.trim() : null))
        .catch(() => null),
    staleTime: 300_000, // 5 min
    enabled: !!(settings?.agent_configured || generatedSecret),
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
      apiClient.put(`/settings/ai-providers/${provider}`, {
        api_key: key,
        ...(modelInput.trim() && { model: modelInput.trim() }),
      }),
    onSuccess: () => {
      refetchAIProviders();
      setEditingProvider(null);
      setApiKeyInput("");
      setModelInput("");
    },
  });

  const setDefaultMutation = useMutation({
    mutationFn: (provider: string) =>
      apiClient.put("/settings/ai-providers/default", { provider }),
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

  function copyCmd(key: string, text: string) {
    navigator.clipboard.writeText(text);
    setCopiedCmd(key);
    setTimeout(() => setCopiedCmd(null), 2000);
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
                    {info?.model && (
                      <span className="text-xs text-gray-400">· {info.model}</span>
                    )}
                  </div>
                  {isEditing && (
                    <div className="mt-2 space-y-1.5">
                      <input
                        type="password"
                        value={apiKeyInput}
                        onChange={(e) => setApiKeyInput(e.target.value)}
                        placeholder={provider === "anthropic" ? "sk-ant-..." : "sk-..."}
                        className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-indigo-500"
                        autoFocus
                      />
                      <input
                        type="text"
                        value={modelInput}
                        onChange={(e) => setModelInput(e.target.value)}
                        placeholder={`Model (default: ${provider === "anthropic" ? "claude-sonnet-4-6" : "gpt-4o"})`}
                        className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-indigo-500"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={() => setProviderMutation.mutate({ provider, key: apiKeyInput })}
                          disabled={setProviderMutation.isPending || !apiKeyInput}
                          className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button onClick={() => { setEditingProvider(null); setModelInput(""); }} className="text-xs text-gray-500">
                          Cancel
                        </button>
                      </div>
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

        {(settings?.agent_configured || generatedSecret) && (() => {
          const secret = generatedSecret ?? "<YOUR-SECRET>";
          const controlPlane = window.location.origin;
          const downloadBase = (import.meta.env.VITE_AGENT_DOWNLOAD_URL as string) || controlPlane;

          const version = agentVersion ?? "<VERSION>";
          const binaryName = agentPlatform === "windows"
            ? `nexplane-agent-windows-amd64-${version}.exe`
            : agentPlatform === "linux-arm64"
            ? `nexplane-agent-linux-arm64-${version}`
            : `nexplane-agent-linux-amd64-${version}`;

          const agentBin = agentPlatform === "windows" ? "nexplane-agent.exe" : "nexplane-agent";

          const linuxDownload = `curl -fsSL ${downloadBase}/downloads/${binaryName} -o ${agentBin} && chmod +x ${agentBin}`;
          const linuxVerify = `curl -fsSL ${downloadBase}/downloads/${binaryName}.sha256 | awk '{print $1 "  ${agentBin}"}' | sha256sum -c`;
          const linuxEphemeral = `./${agentBin} \\\n  --control-plane ${controlPlane} \\\n  --secret ${secret} \\\n  --mode ephemeral`;
          const linuxService = `sudo ./${agentBin} \\\n  --control-plane ${controlPlane} \\\n  --secret ${secret} \\\n  --mode service \\\n  --poll-interval 30s`;
          const linuxSystemd = `[Unit]\nDescription=Nexplane Agent\nAfter=network.target\n\n[Service]\nExecStart=/usr/local/bin/nexplane-agent \\\n  --control-plane ${controlPlane} \\\n  --secret ${secret} \\\n  --mode service \\\n  --poll-interval 30s\nRestart=on-failure\n\n[Install]\nWantedBy=multi-user.target`;
          const winDownload = `Invoke-WebRequest -Uri "${downloadBase}/downloads/${binaryName}" -OutFile ${agentBin}`;
          const winEphemeral = `.\\${agentBin} \`\n  --control-plane ${controlPlane} \`\n  --secret ${secret} \`\n  --mode ephemeral`;
          const winService = `New-Service -Name "NexplaneAgent" \`\n  -BinaryPathName "C:\\nexplane\\nexplane-agent.exe --mode service --poll-interval 30s --control-plane ${controlPlane} --secret ${secret}" \`\n  -StartupType Automatic\nStart-Service NexplaneAgent`;

          const CmdBlock = ({ id, label, value }: { id: string; label: string; value: string }) => (
            <div className="mt-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-slate-500">{label}</span>
                <button
                  onClick={() => copyCmd(id, value)}
                  className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700"
                >
                  {copiedCmd === id ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
                  {copiedCmd === id ? "Copied" : "Copy"}
                </button>
              </div>
              <pre className="bg-slate-950 text-slate-100 text-xs rounded p-3 overflow-x-auto whitespace-pre">{value}</pre>
            </div>
          );

          return (
            <div className="mt-4 border-t border-slate-100 pt-4">
              <h3 className="text-xs font-semibold text-slate-700 mb-3">Deploy Agent</h3>
              {!generatedSecret && (
                <p className="text-xs text-slate-400 mb-3">
                  Replace <code className="font-mono bg-slate-100 px-1 rounded">&lt;YOUR-SECRET&gt;</code> with the secret from when you generated it. Rotate to get a new one.
                </p>
              )}
              <div className="flex gap-2 mb-3">
                {([
                  { id: "linux",      label: "🐧 Linux (x86_64)" },
                  { id: "linux-arm64", label: "🐧 Linux (ARM64)" },
                  { id: "windows",    label: "🪟 Windows" },
                ] as const).map((p) => (
                  <button
                    key={p.id}
                    onClick={() => setAgentPlatform(p.id)}
                    className={`px-3 py-1 text-xs rounded-md border transition-colors ${
                      agentPlatform === p.id
                        ? "bg-slate-900 text-white border-slate-900"
                        : "border-slate-200 text-slate-600 hover:bg-slate-50"
                    }`}
                  >
                    {p.label}
                  </button>
                ))}
              </div>

              {agentPlatform !== "windows" ? (
                <>
                  <CmdBlock id="linux-download"  label="1. Download binary" value={linuxDownload} />
                  <CmdBlock id="linux-verify"    label="2. Verify checksum" value={linuxVerify} />
                  <CmdBlock id="linux-ephemeral" label="3. Run once (ephemeral)" value={linuxEphemeral} />
                  <CmdBlock id="linux-service"   label="Run as foreground service" value={linuxService} />
                  <CmdBlock id="linux-systemd"   label="systemd unit (save to /etc/systemd/system/nexplane-agent.service)" value={linuxSystemd} />
                </>
              ) : (
                <>
                  <CmdBlock id="win-download"  label="1. Download binary (PowerShell)" value={winDownload} />
                  <CmdBlock id="win-ephemeral" label="2. Run once (ephemeral)" value={winEphemeral} />
                  <CmdBlock id="win-service"   label="Install as Windows Service (run as admin)" value={winService} />
                </>
              )}
            </div>
          );
        })()}
      </div>

    </div>
  );
}
