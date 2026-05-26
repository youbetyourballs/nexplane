import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Plus, Trash2, Copy, Check, X } from "lucide-react";
import { apiClient } from "../api/client";

interface AgentToken {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked: boolean;
  allowed_connector_types: string[];
  allowed_cr_types: string[];
  allowed_roles: string[];
  allowed_asset_tags: string[];
}

interface AgentTokenCreatedResponse {
  id: string;
  name: string;
  raw_token: string;
  expires_at: string | null;
  created_at: string;
}

function fetchAgentTokens(): Promise<AgentToken[]> {
  return apiClient.get("/auth/agent-tokens").then((r) => r.data);
}

function generateAgentToken(payload: {
  name: string;
  expires_in_days: number | null;
  allowed_roles: string[];
  allowed_cr_types: string[];
  allowed_connector_types: string[];
}): Promise<AgentTokenCreatedResponse> {
  return apiClient.post("/auth/agent-tokens", payload).then((r) => r.data);
}

function revokeAgentToken(id: string): Promise<void> {
  return apiClient.delete(`/auth/agent-tokens/${id}`).then(() => undefined);
}

function Chip({ label }: { label: string }) {
  return (
    <span className="inline-block bg-slate-100 text-slate-600 text-xs px-1.5 py-0.5 rounded mr-1 mb-0.5">
      {label}
    </span>
  );
}

function parseCsv(s: string): string[] {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);
}

export function AgentTokenManager() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [expiresInDays, setExpiresInDays] = useState("");
  const [allowedRoles, setAllowedRoles] = useState("");
  const [allowedCrTypes, setAllowedCrTypes] = useState("");
  const [allowedConnectorTypes, setAllowedConnectorTypes] = useState("");
  const [newToken, setNewToken] = useState<AgentTokenCreatedResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const { data: tokens = [], isLoading } = useQuery({
    queryKey: ["agent-tokens"],
    queryFn: fetchAgentTokens,
  });

  const createMut = useMutation({
    mutationFn: () =>
      generateAgentToken({
        name: name.trim(),
        expires_in_days: expiresInDays ? parseInt(expiresInDays, 10) : null,
        allowed_roles: parseCsv(allowedRoles),
        allowed_cr_types: parseCsv(allowedCrTypes),
        allowed_connector_types: parseCsv(allowedConnectorTypes),
      }),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["agent-tokens"] });
      setNewToken(data);
      setShowForm(false);
      setName("");
      setExpiresInDays("");
      setAllowedRoles("");
      setAllowedCrTypes("");
      setAllowedConnectorTypes("");
    },
  });

  const revokeMut = useMutation({
    mutationFn: revokeAgentToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-tokens"] }),
  });

  function handleCopy() {
    if (!newToken) return;
    navigator.clipboard.writeText(newToken.raw_token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function formatDate(iso: string | null) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString();
  }

  const activeTokens = tokens.filter((t) => !t.revoked);

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-6 mb-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-slate-500" />
          <h3 className="font-semibold text-slate-800">Agent Tokens</h3>
        </div>
        <button
          onClick={() => { setShowForm(true); setNewToken(null); }}
          className="flex items-center gap-1.5 text-sm bg-indigo-600 text-white px-3 py-1.5 rounded-md hover:bg-indigo-700"
        >
          <Plus className="w-3.5 h-3.5" />
          Generate Agent Token
        </button>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        Agent tokens are scoped credentials for automated MCP clients. Unlike API tokens, they
        carry explicit scope constraints (connector types, CR types, roles) that are enforced on
        every request. Use them to safely delegate platform access to AI agents.
      </p>

      {/* New token revealed */}
      {newToken && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-lg p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-amber-800 mb-1">
                Agent token created — copy it now. It won't be shown again.
              </p>
              <code className="block text-xs bg-amber-100 border border-amber-200 rounded p-2 break-all font-mono">
                {newToken.raw_token}
              </code>
            </div>
            <div className="flex gap-1 shrink-0">
              <button
                onClick={handleCopy}
                className="flex items-center gap-1 text-xs text-amber-700 border border-amber-300 px-2 py-1 rounded hover:bg-amber-100"
              >
                {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                {copied ? "Copied" : "Copy"}
              </button>
              <button onClick={() => setNewToken(null)} className="text-amber-600 hover:text-amber-800">
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Generate form */}
      {showForm && (
        <div className="mb-4 bg-slate-50 border border-slate-200 rounded-lg p-4">
          <p className="text-sm font-medium text-slate-700 mb-3">New agent token</p>
          <div className="space-y-2">
            <input
              type="text"
              placeholder="Label (e.g. Claude Code — patch automation)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
            <div>
              <label className="text-xs text-slate-500 mb-1 block">Expiry in days (optional)</label>
              <input
                type="number"
                placeholder="e.g. 90"
                value={expiresInDays}
                onChange={(e) => setExpiresInDays(e.target.value)}
                className="text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400 w-32"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block">
                Allowed roles (comma-separated, e.g. <code>read,write</code>)
              </label>
              <input
                type="text"
                placeholder="read, write"
                value={allowedRoles}
                onChange={(e) => setAllowedRoles(e.target.value)}
                className="w-full text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block">
                Allowed CR types (comma-separated, e.g. <code>patch_packages,rotate_key</code>)
              </label>
              <input
                type="text"
                placeholder="patch_packages, rotate_key"
                value={allowedCrTypes}
                onChange={(e) => setAllowedCrTypes(e.target.value)}
                className="w-full text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block">
                Allowed connector types (comma-separated, e.g. <code>aws,ssh</code>)
              </label>
              <input
                type="text"
                placeholder="aws, ssh"
                value={allowedConnectorTypes}
                onChange={(e) => setAllowedConnectorTypes(e.target.value)}
                className="w-full text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
              />
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={() => createMut.mutate()}
              disabled={!name.trim() || createMut.isPending}
              className="text-sm bg-indigo-600 text-white px-3 py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50"
            >
              {createMut.isPending ? "Generating…" : "Generate"}
            </button>
            <button
              onClick={() => {
                setShowForm(false);
                setName("");
                setExpiresInDays("");
                setAllowedRoles("");
                setAllowedCrTypes("");
                setAllowedConnectorTypes("");
              }}
              className="text-sm text-slate-600 border border-slate-300 px-3 py-1.5 rounded hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Token list */}
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : activeTokens.length === 0 ? (
        <p className="text-sm text-slate-400">No active agent tokens.</p>
      ) : (
        <div className="divide-y divide-slate-100">
          {activeTokens.map((t) => (
            <div key={t.id} className="py-3 gap-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium text-slate-700 truncate">{t.name}</p>
                <button
                  onClick={() => revokeMut.mutate(t.id)}
                  disabled={revokeMut.isPending}
                  className="flex items-center gap-1 text-xs text-red-600 border border-red-200 px-2 py-1 rounded hover:bg-red-50 disabled:opacity-50 shrink-0 ml-3"
                >
                  <Trash2 className="w-3 h-3" />
                  Revoke
                </button>
              </div>
              <p className="text-xs text-slate-400 mt-0.5">
                Created {formatDate(t.created_at)}
                {t.last_used_at && ` · Last used ${formatDate(t.last_used_at)}`}
                {t.expires_at && ` · Expires ${formatDate(t.expires_at)}`}
              </p>
              <div className="mt-1.5 flex flex-wrap gap-y-1">
                {t.allowed_roles.map((r) => (
                  <Chip key={`role-${r}`} label={`role:${r}`} />
                ))}
                {t.allowed_connector_types.map((c) => (
                  <Chip key={`conn-${c}`} label={`connector:${c}`} />
                ))}
                {t.allowed_cr_types.map((c) => (
                  <Chip key={`cr-${c}`} label={`cr:${c}`} />
                ))}
                {t.allowed_asset_tags.map((tag) => (
                  <Chip key={`tag-${tag}`} label={`tag:${tag}`} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
