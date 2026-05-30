// frontend/src/components/SecurityPolicySoakPanel.tsx
import { useState } from "react";
import { useAuth } from "../context/AuthContext";

interface Asset {
  id: string;
  name: string;
}

interface SoakSession {
  id: string;
  status: string;
  policy_type: string;
  window_seconds: number;
  synthesized_profile: { syscalls: { names: string[] }[] } | null;
  baseline_delta: { added: string[]; removed: string[] } | null;
  cr_id: string | null;
  partial: boolean;
  stopped_at: string | null;
  asset_ids: string[];
}

interface Props {
  projectId: string;
  assets: Asset[];
}

export function SecurityPolicySoakPanel({ projectId, assets }: Props) {
  const { token } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [session, setSession] = useState<SoakSession | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [windowSeconds, setWindowSeconds] = useState(600);
  const [serviceName, setServiceName] = useState("");

  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  async function startSession() {
    if (!selectedAssets.length || !serviceName) {
      setError("Select at least one asset and enter a service name.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/security-policy/soak-sessions", {
        method: "POST",
        headers,
        body: JSON.stringify({
          project_id: projectId,
          policy_type: "seccomp",
          asset_ids: selectedAssets,
          window_seconds: windowSeconds,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSession(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function stopSession() {
    if (!session || !serviceName) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/security-policy/soak-sessions/${session.id}/stop`, {
        method: "POST",
        headers,
        body: JSON.stringify({ service_name: serviceName }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSession(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function acceptDiff() {
    if (!session || !serviceName) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/security-policy/soak-sessions/${session.id}/accept`, {
        method: "POST",
        headers,
        body: JSON.stringify({ service_name: serviceName }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSession(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const syscallCount = session?.synthesized_profile?.syscalls?.[0]?.names?.length ?? 0;
  const delta = session?.baseline_delta;

  return (
    <div className="mt-4 border border-slate-200 rounded-lg bg-white">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-slate-700 hover:bg-slate-50"
        onClick={() => setExpanded(!expanded)}
      >
        <span>Security Policy Soak</span>
        <span className="text-slate-400">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-slate-100">
          {error && (
            <div className="text-red-600 text-xs mt-2">{error}</div>
          )}

          {!session && (
            <>
              <div className="mt-3">
                <label className="block text-xs text-slate-500 mb-1">Service name (e.g. nginx)</label>
                <input
                  className="w-full border border-slate-300 rounded px-2 py-1 text-sm"
                  value={serviceName}
                  onChange={e => setServiceName(e.target.value)}
                  placeholder="nginx"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">
                  Window: {Math.round(windowSeconds / 60)} min
                </label>
                <input
                  type="range" min={30} max={3600} step={30}
                  value={windowSeconds}
                  onChange={e => setWindowSeconds(Number(e.target.value))}
                  className="w-full"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Assets to observe</label>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {assets.map(a => (
                    <label key={a.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={selectedAssets.includes(a.id)}
                        onChange={e => setSelectedAssets(
                          e.target.checked
                            ? [...selectedAssets, a.id]
                            : selectedAssets.filter(x => x !== a.id)
                        )}
                      />
                      {a.name}
                    </label>
                  ))}
                </div>
              </div>
              <button
                className="w-full bg-indigo-600 text-white text-sm py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50"
                onClick={startSession}
                disabled={loading}
              >
                {loading ? "Starting..." : "Start Soak Session"}
              </button>
            </>
          )}

          {session && session.status === "running" && (
            <div className="mt-3 space-y-2">
              <div className="text-sm text-slate-600">
                Session running — observing {session.asset_ids?.length ?? "?"} assets for {Math.round(session.window_seconds / 60)} min.
              </div>
              <input
                className="w-full border border-slate-300 rounded px-2 py-1 text-sm"
                value={serviceName}
                onChange={e => setServiceName(e.target.value)}
                placeholder="Service name (required to stop)"
              />
              <button
                className="w-full bg-orange-600 text-white text-sm py-1.5 rounded hover:bg-orange-700 disabled:opacity-50"
                onClick={stopSession}
                disabled={loading}
              >
                {loading ? "Stopping..." : "Stop & Synthesize"}
              </button>
            </div>
          )}

          {session && session.status === "synthesized" && delta && (
            <div className="mt-3 space-y-3">
              <div className="text-sm font-medium text-slate-700">Profile diff vs baseline</div>
              {session.partial && (
                <div className="text-xs text-yellow-700 bg-yellow-50 border border-yellow-200 rounded px-2 py-1">
                  Partial observation — some assets were unreachable
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-green-700 font-medium mb-1">Added ({delta.added.length})</div>
                  <div className="max-h-32 overflow-y-auto text-xs font-mono text-green-800 bg-green-50 rounded p-2 space-y-0.5">
                    {delta.added.length === 0
                      ? <span className="text-slate-400">none</span>
                      : delta.added.map(s => <div key={s}>{s}</div>)}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-red-700 font-medium mb-1">Removed ({delta.removed.length})</div>
                  <div className="max-h-32 overflow-y-auto text-xs font-mono text-red-800 bg-red-50 rounded p-2 space-y-0.5">
                    {delta.removed.length === 0
                      ? <span className="text-slate-400">none</span>
                      : delta.removed.map(s => <div key={s}>{s}</div>)}
                  </div>
                </div>
              </div>
              <button
                className="w-full bg-indigo-600 text-white text-sm py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50"
                onClick={acceptDiff}
                disabled={loading}
              >
                {loading ? "Proposing..." : "Accept & Propose CR"}
              </button>
              <button
                className="w-full border border-slate-300 text-slate-600 text-sm py-1.5 rounded hover:bg-slate-50"
                onClick={() => setSession(null)}
              >
                Discard
              </button>
            </div>
          )}

          {session && (session.status === "cr_proposed" || (session.status === "synthesized" && !delta)) && (
            <div className="mt-3 space-y-2">
              <div className="text-sm text-green-700 font-medium">
                {syscallCount} syscalls — configure_seccomp CR proposed
              </div>
              {session.cr_id && (
                <a
                  href={`/change-requests/${session.cr_id}`}
                  className="text-indigo-600 text-sm underline"
                >
                  View CR →
                </a>
              )}
              {session.partial && (
                <div className="text-xs text-yellow-700 bg-yellow-50 border border-yellow-200 rounded px-2 py-1">
                  Partial observation — profile may not be complete
                </div>
              )}
              <button
                className="text-xs text-slate-400 hover:text-slate-600"
                onClick={() => setSession(null)}
              >
                Start new session
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
