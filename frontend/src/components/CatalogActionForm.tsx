// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useState, useCallback } from "react";
import { ChevronDown } from "lucide-react";
import type { CatalogAction, CatalogParam } from "../types/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function coerce(p: CatalogParam, raw: unknown): unknown {
  if (p.type === "boolean") return Boolean(raw);
  if (p.type === "number" || p.type === "integer")
    return raw === "" || raw == null ? undefined : Number(raw);
  if (p.type === "array") {
    if (Array.isArray(raw)) return raw;
    return [];
  }
  return raw;
}

function maskSecret(v: unknown): string {
  if (v === "" || v == null) return "—";
  return "••••••••";
}

function displayValue(p: CatalogParam, v: unknown): string {
  if (p.secret) return maskSecret(v);
  if (Array.isArray(v)) return v.length === 0 ? "—" : v.join(", ");
  if (v === "" || v == null) return "—";
  return String(v);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ArrayEnumField({
  p,
  value,
  onChange,
}: {
  p: CatalogParam;
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const toggle = (opt: string) => {
    if (value.includes(opt)) onChange(value.filter((x) => x !== opt));
    else onChange([...value, opt]);
  };
  return (
    <div className="flex flex-wrap gap-2">
      {(p.enum ?? []).map((opt) => (
        <label key={opt} className="flex items-center gap-1.5 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={value.includes(opt)}
            onChange={() => toggle(opt)}
            className="rounded border-slate-300 text-brand-600"
          />
          {opt}
        </label>
      ))}
    </div>
  );
}

function TagInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const add = useCallback(() => {
    const t = draft.trim();
    if (t && !value.includes(t)) onChange([...value, t]);
    setDraft("");
  }, [draft, value, onChange]);

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-1 min-h-[32px] p-1.5 border border-slate-300 rounded text-sm">
        {value.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 px-2 py-0.5 bg-brand-100 text-brand-800 rounded text-xs"
          >
            {tag}
            <button
              type="button"
              onClick={() => onChange(value.filter((t) => t !== tag))}
              className="hover:text-brand-900 leading-none"
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); add(); }
            if (e.key === "Backspace" && draft === "" && value.length > 0)
              onChange(value.slice(0, -1));
          }}
          placeholder={value.length === 0 ? "Type and press Enter…" : ""}
          className="flex-1 min-w-[120px] outline-none bg-transparent text-sm"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single field renderer
// ---------------------------------------------------------------------------

function FieldRow({
  p,
  val,
  error,
  touched,
  onChange,
  onBlur,
}: {
  p: CatalogParam;
  val: unknown;
  error: string | undefined;
  touched: boolean;
  onChange: (name: string, v: unknown) => void;
  onBlur: (name: string) => void;
}) {
  const strVal = String(val ?? "");
  const arrVal = Array.isArray(val) ? val : [];
  const hasError = touched && !!error;
  const borderCls = hasError
    ? "border-red-400 focus:ring-red-400"
    : "border-slate-300 focus:ring-brand-500";
  const inputCls = `w-full px-3 py-2 border rounded-md text-sm focus:outline-none focus:ring-2 ${borderCls}`;

  let field: React.ReactNode;

  if (p.type === "boolean") {
    field = (
      <input
        id={`f-${p.name}`}
        type="checkbox"
        checked={Boolean(val)}
        onChange={(e) => onChange(p.name, e.target.checked)}
        className="rounded border-slate-300 text-brand-600"
      />
    );
  } else if (p.type === "array" && p.enum) {
    field = (
      <ArrayEnumField
        p={p}
        value={arrVal as string[]}
        onChange={(v) => onChange(p.name, v)}
      />
    );
  } else if (p.type === "array") {
    field = (
      <TagInput
        value={arrVal as string[]}
        onChange={(v) => onChange(p.name, v)}
      />
    );
  } else {
    field = (
      <div className="space-y-0.5">
        <input
          id={`f-${p.name}`}
          type={p.secret ? "password" : p.type === "number" || p.type === "integer" ? "number" : "text"}
          value={strVal}
          onChange={(e) => onChange(p.name, e.target.value)}
          onBlur={() => onBlur(p.name)}
          className={inputCls}
        />
        {p.maxLength != null && (
          <p className="text-xs text-slate-400 text-right">
            {strVal.length} / {p.maxLength}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={`f-${p.name}`} className="block text-sm font-medium text-slate-700">
        {p.name}
        {p.required && <span className="text-red-500 ml-0.5">*</span>}
        {p.description && (
          <span className="text-xs font-normal text-slate-400 ml-1">— {p.description}</span>
        )}
      </label>
      {field}
      {hasError && <p className="text-xs text-red-500">{error}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function CatalogActionForm({
  action,
  initial = {},
  onSubmit,
  submitting = false,
}: {
  action: CatalogAction;
  initial?: Record<string, unknown>;
  onSubmit: (params: Record<string, unknown>) => void;
  submitting?: boolean;
}) {
  const [vals, setVals] = useState<Record<string, unknown>>(() => {
    const v: Record<string, unknown> = {};
    for (const p of action.param_schema) {
      if (p.type === "array") {
        v[p.name] = Array.isArray(initial[p.name]) ? initial[p.name] : [];
      } else {
        v[p.name] = initial[p.name] ?? p.default ?? (p.type === "boolean" ? false : "");
      }
    }
    return v;
  });

  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [step, setStep] = useState<"form" | "review">("form");

  const required = action.param_schema.filter((p) => p.required);
  const optional = action.param_schema.filter((p) => !p.required);
  const hasBothGroups = required.length > 0 && optional.length > 0;

  const validate = (name: string, v: unknown, p: CatalogParam): string | undefined => {
    if (p.required) {
      if (p.type === "array" && Array.isArray(v) && v.length === 0) return "Required";
      if (p.type !== "array" && (v === "" || v == null)) return "Required";
    }
    if (p.pattern && typeof v === "string" && v !== "") {
      try {
        if (!new RegExp(p.pattern).test(v)) return `Must match pattern: ${p.pattern}`;
      } catch {
        // invalid regex in schema — skip
      }
    }
    if (p.maxLength != null && typeof v === "string" && v.length > p.maxLength)
      return `Max ${p.maxLength} characters`;
    return undefined;
  };

  const fieldErrors: Record<string, string | undefined> = {};
  for (const p of action.param_schema) {
    const e = validate(p.name, vals[p.name], p);
    if (e) fieldErrors[p.name] = e;
  }
  const hasErrors = Object.keys(fieldErrors).length > 0;

  const handleChange = (name: string, v: unknown) => {
    setVals((prev) => ({ ...prev, [name]: v }));
  };

  const handleBlur = (name: string) => {
    setTouched((prev) => ({ ...prev, [name]: true }));
  };

  const touchAll = () => {
    const t: Record<string, boolean> = {};
    for (const p of action.param_schema) t[p.name] = true;
    setTouched(t);
  };

  const handleSubmitClick = () => {
    touchAll();
    if (hasErrors) return;
    if (action.destructive) {
      setStep("review");
      return;
    }
    doSubmit();
  };

  const doSubmit = () => {
    const out: Record<string, unknown> = {};
    for (const p of action.param_schema) {
      const c = coerce(p, vals[p.name]);
      if (p.type === "boolean") { out[p.name] = Boolean(c); continue; }
      if (p.type === "array") { out[p.name] = c; continue; }
      if (c !== undefined && c !== "") out[p.name] = c;
    }
    onSubmit(out);
  };

  const renderField = (p: CatalogParam) => (
    <FieldRow
      key={p.name}
      p={p}
      val={vals[p.name]}
      error={fieldErrors[p.name]}
      touched={!!touched[p.name]}
      onChange={handleChange}
      onBlur={handleBlur}
    />
  );

  // ---- Review step --------------------------------------------------------
  if (step === "review") {
    return (
      <div className="space-y-4">
        <div className="rounded-md bg-amber-50 border border-amber-200 p-3 text-xs text-amber-800">
          <strong>Destructive action.</strong> Please review before confirming.
        </div>
        <div>
          <p className="text-sm font-semibold text-slate-900">{action.display_name}</p>
          {action.description && (
            <p className="text-xs text-slate-500 mt-0.5">{action.description}</p>
          )}
        </div>
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-md overflow-hidden">
          {action.param_schema.map((p) => (
            <div key={p.name} className="flex items-start px-3 py-2 text-sm">
              <span className="w-40 shrink-0 text-slate-500 text-xs font-medium pt-0.5">{p.name}</span>
              <span className="text-slate-900 break-all">{displayValue(p, vals[p.name])}</span>
            </div>
          ))}
        </div>
        <div className="flex gap-3 pt-1">
          <button
            type="button"
            onClick={() => setStep("form")}
            className="px-4 py-2 text-sm text-slate-600 border border-slate-300 rounded-md hover:bg-slate-50"
          >
            Back
          </button>
          <button
            type="button"
            onClick={doSubmit}
            disabled={submitting}
            className="px-4 py-2 text-sm font-medium text-white bg-red-600 rounded-md hover:bg-red-700 disabled:opacity-50"
          >
            {submitting ? "Running…" : "Confirm"}
          </button>
        </div>
      </div>
    );
  }

  // ---- Form step ----------------------------------------------------------
  return (
    <div className="space-y-4">
      {/* Required section */}
      {hasBothGroups && required.length > 0 && (
        <div className="space-y-3">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Required</p>
          {required.map(renderField)}
        </div>
      )}
      {!hasBothGroups && required.map(renderField)}

      {/* Optional section */}
      {hasBothGroups && optional.length > 0 && (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => setShowAdvanced((v) => !v)}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"
          >
            <ChevronDown
              className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? "rotate-180" : ""}`}
            />
            Show advanced options
          </button>
          {showAdvanced && (
            <div className="space-y-3">
              {optional.map(renderField)}
            </div>
          )}
        </div>
      )}
      {!hasBothGroups && optional.map(renderField)}

      <button
        type="button"
        onClick={handleSubmitClick}
        disabled={submitting}
        className="text-sm px-3 py-1.5 bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50"
      >
        {submitting ? "Running…" : action.destructive ? "Review & Run" : "Run"}
      </button>
    </div>
  );
}
