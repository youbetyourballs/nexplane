// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { agentTunnelsApi } from "../api/endpoints";
import type { AgentTunnelStatus } from "../types/api";
import { useAuth } from "../hooks/useAuth";
import { RFC1918_DEFAULT_ALLOWLIST, validateAllowlistEntry } from "../lib/allowlist";

export default function AgentTunnelManager({ agentId }: { agentId?: string }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const { data: agents = [], isLoading } = useQuery({
    queryKey: ["agent-tunnels"],
    queryFn: agentTunnelsApi.list,
    refetchInterval: 10_000,
  });
  const setMut = useMutation({
    mutationFn: ({ id, enabled, allowlist }: { id: string; enabled: boolean; allowlist: string[] }) =>
      agentTunnelsApi.set(id, { enabled, allowlist }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["agent-tunnels"] }),
  });

  if (user?.role !== "admin") return <p className="text-sm text-gray-500">Admin access required.</p>;
  if (isLoading) return <p className="text-sm text-gray-500">Loading agents...</p>;

  const shown = agentId ? agents.filter((a) => a.agent_id === agentId) : agents;
  if (!shown.length) return <p className="text-sm text-gray-500">No agents.</p>;

  return (
    <div className="space-y-4">
      {shown.map((a) => (
        <AgentRow key={a.agent_id} agent={a} pending={setMut.isPending}
          onToggle={(enabled) => {
            const allowlist = enabled && a.tunnel_allowlist.length === 0 ? RFC1918_DEFAULT_ALLOWLIST : a.tunnel_allowlist;
            setMut.mutate({ id: a.agent_id, enabled, allowlist });
          }}
          onSave={(allowlist) => setMut.mutate({ id: a.agent_id, enabled: a.tunnel_enabled, allowlist })}
        />
      ))}
      {setMut.isError && <p className="text-sm text-red-500">Failed to update tunnel.</p>}
    </div>
  );
}

function AgentRow({ agent, pending, onToggle, onSave }: {
  agent: AgentTunnelStatus; pending: boolean;
  onToggle: (enabled: boolean) => void; onSave: (allowlist: string[]) => void;
}) {
  const [entries, setEntries] = useState<string[]>(agent.tunnel_allowlist);
  const [draft, setDraft] = useState("");
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => setEntries(agent.tunnel_allowlist), [agent.tunnel_allowlist]);

  const add = () => {
    const msg = validateAllowlistEntry(draft);
    if (msg) { setErr(msg); return; }
    setEntries([...entries, draft.trim()]); setDraft(""); setErr(null);
  };

  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="font-medium text-gray-900">{agent.hostname}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded ${agent.online ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>
            {agent.online ? "online" : "offline"}
          </span>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" aria-label="Enable tunnel" checked={agent.tunnel_enabled}
            disabled={pending} onChange={(e) => onToggle(e.target.checked)} />
          Enabled
        </label>
      </div>
      <div className="flex flex-wrap gap-1 mb-2">
        {entries.map((e, i) => (
          <span key={`${e}-${i}`} className="inline-flex items-center bg-slate-100 text-slate-600 text-xs px-1.5 py-0.5 rounded font-mono">
            {e}<button className="ml-1 text-slate-400 hover:text-red-500" onClick={() => setEntries(entries.filter((_, j) => j !== i))}>&times;</button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add destination (host:port)"
          className="border border-slate-300 rounded px-2 py-1 text-sm font-mono" />
        <button onClick={add} className="text-sm px-2 py-1 border rounded hover:bg-slate-50">Add</button>
        <button onClick={() => onSave(entries)} disabled={pending}
          className="text-sm px-2 py-1 bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50">Save</button>
      </div>
      {err && <p className="text-xs text-red-500 mt-1">{err}</p>}
    </div>
  );
}
