// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Key, Plus, Trash2, Copy, Check, X } from "lucide-react";
import { apiClient } from "../api/client";

interface ApiToken {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
  revoked: boolean;
}

interface TokenCreatedResponse {
  id: string;
  name: string;
  raw_token: string;
  expires_at: string | null;
  created_at: string;
}

function fetchTokens(): Promise<ApiToken[]> {
  return apiClient.get("/tokens").then((r) => r.data);
}

function generateToken(name: string, expires_at: string | null): Promise<TokenCreatedResponse> {
  return apiClient.post("/tokens", { name, expires_at }).then((r) => r.data);
}

function revokeToken(id: string): Promise<void> {
  return apiClient.delete(`/tokens/${id}`).then(() => undefined);
}

export function ApiTokenManager() {
  const qc = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [newToken, setNewToken] = useState<TokenCreatedResponse | null>(null);
  const [copied, setCopied] = useState(false);

  const { data: tokens = [], isLoading } = useQuery({
    queryKey: ["api-tokens"],
    queryFn: fetchTokens,
  });

  const createMut = useMutation({
    mutationFn: () => generateToken(name.trim(), expiresAt || null),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["api-tokens"] });
      setNewToken(data);
      setShowForm(false);
      setName("");
      setExpiresAt("");
    },
  });

  const revokeMut = useMutation({
    mutationFn: revokeToken,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["api-tokens"] }),
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

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-6 mb-4">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Key className="w-4 h-4 text-slate-500" />
          <h3 className="font-semibold text-slate-800">API Tokens</h3>
        </div>
        <button
          onClick={() => { setShowForm(true); setNewToken(null); }}
          className="flex items-center gap-1.5 text-sm bg-indigo-600 text-white px-3 py-1.5 rounded-md hover:bg-indigo-700"
        >
          <Plus className="w-3.5 h-3.5" />
          Generate token
        </button>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        Use API tokens to authenticate MCP clients (Claude Code, Claude Desktop) against the
        Nexplane platform. Configure your client with{" "}
        <code className="bg-slate-100 px-1 rounded text-xs">Authorization: Bearer &lt;token&gt;</code>.
        Tokens inherit your role — an analyst token cannot approve Change Requests.
      </p>

      {/* New token revealed */}
      {newToken && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-lg p-4">
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-amber-800 mb-1">
                Token created — copy it now. It won't be shown again.
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
          <p className="text-sm font-medium text-slate-700 mb-3">New API token</p>
          <div className="space-y-2">
            <input
              type="text"
              placeholder="Label (e.g. Claude Code — dev laptop)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
            />
            <div>
              <label className="text-xs text-slate-500 mb-1 block">Expiry (optional)</label>
              <input
                type="date"
                value={expiresAt}
                onChange={(e) => setExpiresAt(e.target.value)}
                className="text-sm border border-slate-300 rounded px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-400"
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
              onClick={() => { setShowForm(false); setName(""); setExpiresAt(""); }}
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
      ) : tokens.length === 0 ? (
        <p className="text-sm text-slate-400">No API tokens yet.</p>
      ) : (
        <div className="divide-y divide-slate-100">
          {tokens.map((t) => (
            <div key={t.id} className="flex items-center justify-between py-2.5 gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-slate-700 truncate">{t.name}</p>
                <p className="text-xs text-slate-400">
                  Created {formatDate(t.created_at)}
                  {t.last_used_at && ` · Last used ${formatDate(t.last_used_at)}`}
                  {t.expires_at && ` · Expires ${formatDate(t.expires_at)}`}
                </p>
              </div>
              <button
                onClick={() => revokeMut.mutate(t.id)}
                disabled={revokeMut.isPending}
                className="flex items-center gap-1 text-xs text-red-600 border border-red-200 px-2 py-1 rounded hover:bg-red-50 disabled:opacity-50 shrink-0"
              >
                <Trash2 className="w-3 h-3" />
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
