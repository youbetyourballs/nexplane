// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useState } from "react";
import type { CatalogAction, CatalogParam } from "../types/api";

function coerce(p: CatalogParam, raw: unknown): unknown {
  if (p.type === "boolean") return Boolean(raw);
  if (p.type === "number" || p.type === "integer") return raw === "" || raw == null ? undefined : Number(raw);
  return raw;
}

export default function CatalogActionForm({ action, initial = {}, onSubmit, submitting = false }: {
  action: CatalogAction; initial?: Record<string, unknown>; onSubmit: (params: Record<string, unknown>) => void; submitting?: boolean;
}) {
  const [vals, setVals] = useState<Record<string, unknown>>(() => {
    const v: Record<string, unknown> = {};
    for (const p of action.param_schema) v[p.name] = initial[p.name] ?? p.default ?? (p.type === "boolean" ? false : "");
    return v;
  });
  const [err, setErr] = useState<string | null>(null);

  const submit = () => {
    for (const p of action.param_schema) {
      if (p.required && (vals[p.name] === "" || vals[p.name] == null)) { setErr(`${p.name} is required`); return; }
    }
    const out: Record<string, unknown> = {};
    for (const p of action.param_schema) {
      const c = coerce(p, vals[p.name]);
      if (c !== undefined && c !== "") out[p.name] = c;
      else if (p.type === "boolean") out[p.name] = Boolean(c);
    }
    setErr(null); onSubmit(out);
  };

  return (
    <div className="space-y-3">
      {action.param_schema.map((p) => (
        <div key={p.name} className="flex flex-col">
          <label htmlFor={`f-${p.name}`} className="text-xs text-slate-500 mb-0.5">
            {p.name}{p.required ? " *" : ""}{p.description ? ` — ${p.description}` : ""}
          </label>
          {p.type === "boolean" ? (
            <input id={`f-${p.name}`} type="checkbox" checked={Boolean(vals[p.name])}
              onChange={(e) => setVals({ ...vals, [p.name]: e.target.checked })} />
          ) : p.enum ? (
            <select id={`f-${p.name}`} value={String(vals[p.name] ?? "")} onChange={(e) => setVals({ ...vals, [p.name]: e.target.value })}
              className="border border-slate-300 rounded px-2 py-1 text-sm">
              <option value="">—</option>
              {p.enum.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input id={`f-${p.name}`} type={p.secret ? "password" : (p.type === "number" || p.type === "integer") ? "number" : "text"}
              value={String(vals[p.name] ?? "")} onChange={(e) => setVals({ ...vals, [p.name]: e.target.value })}
              className="border border-slate-300 rounded px-2 py-1 text-sm" />
          )}
        </div>
      ))}
      {err && <p className="text-xs text-red-500">{err}</p>}
      <button onClick={submit} disabled={submitting}
        className="text-sm px-3 py-1.5 bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50">
        {submitting ? "Running…" : "Run"}
      </button>
    </div>
  );
}
