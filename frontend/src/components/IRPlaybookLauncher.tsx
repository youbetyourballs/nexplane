import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IRPlaybookTemplate, irApi } from "../api/ir";

interface IRPlaybookLauncherProps {
  onLaunched?: (changeRequest: Record<string, unknown>) => void;
}

function ParameterForm({
  template,
  onSubmit,
  onCancel,
}: {
  template: IRPlaybookTemplate;
  onSubmit: (params: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const [params, setParams] = useState<Record<string, unknown>>(
    Object.fromEntries(
      Object.entries(template.default_parameters).map(([k, v]) => [k, v ?? ""])
    )
  );

  const handleChange = (key: string, value: string) => {
    setParams((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div style={{ padding: 16, background: "#1e293b", borderRadius: 8, minWidth: 320 }}>
      <h3 style={{ margin: "0 0 12px", color: "#f1f5f9" }}>Launch: {template.display_name}</h3>
      {Object.keys(params).map((key) => (
        <div key={key} style={{ marginBottom: 10 }}>
          <label style={{ display: "block", color: "#94a3b8", fontSize: 12, marginBottom: 4 }}>
            {key}
          </label>
          <input
            style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid #334155", background: "#0f172a", color: "#f1f5f9" }}
            value={String(params[key] ?? "")}
            onChange={(e) => handleChange(key, e.target.value)}
          />
        </div>
      ))}
      {/* reason is always required */}
      {!("reason" in params) && (
        <div style={{ marginBottom: 10 }}>
          <label style={{ display: "block", color: "#94a3b8", fontSize: 12, marginBottom: 4 }}>reason</label>
          <input
            style={{ width: "100%", padding: "6px 8px", borderRadius: 4, border: "1px solid #334155", background: "#0f172a", color: "#f1f5f9" }}
            value={String(params["reason"] ?? "")}
            onChange={(e) => handleChange("reason", e.target.value)}
          />
        </div>
      )}
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button
          style={{ flex: 1, padding: "8px 0", background: "#ef4444", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}
          onClick={() => onSubmit(params)}
        >
          Launch
        </button>
        <button
          style={{ flex: 1, padding: "8px 0", background: "#334155", color: "#f1f5f9", border: "none", borderRadius: 4, cursor: "pointer" }}
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

export const IRPlaybookLauncher: React.FC<IRPlaybookLauncherProps> = ({ onLaunched }) => {
  const [selected, setSelected] = useState<IRPlaybookTemplate | null>(null);
  const queryClient = useQueryClient();

  const { data: templates = [], isLoading } = useQuery({
    queryKey: ["ir-templates"],
    queryFn: irApi.getTemplates,
  });

  const launch = useMutation({
    mutationFn: (params: Record<string, unknown>) =>
      irApi.instantiate({ playbook_type: selected!.playbook_type, parameters: params }),
    onSuccess: (cr) => {
      queryClient.invalidateQueries({ queryKey: ["change-requests"] });
      setSelected(null);
      onLaunched?.(cr);
    },
  });

  if (isLoading) return <p style={{ color: "#94a3b8" }}>Loading playbooks...</p>;

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12 }}>
        {templates.map((t) => (
          <div
            key={t.id}
            style={{
              background: "#1e293b",
              borderRadius: 8,
              padding: 16,
              border: "1px solid #334155",
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontWeight: 600, color: "#f1f5f9" }}>{t.display_name}</span>
              {t.ir_auto_approve && (
                <span style={{ fontSize: 10, background: "#16a34a", color: "#fff", borderRadius: 4, padding: "2px 6px" }}>
                  Auto-Approve
                </span>
              )}
            </div>
            <p style={{ margin: 0, fontSize: 12, color: "#94a3b8" }}>{t.description}</p>
            <button
              style={{ marginTop: "auto", padding: "6px 0", background: "#3b82f6", color: "#fff", border: "none", borderRadius: 4, cursor: "pointer" }}
              onClick={() => setSelected(t)}
            >
              Launch
            </button>
          </div>
        ))}
      </div>

      {selected && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <ParameterForm
            template={selected}
            onSubmit={(params) => launch.mutate(params)}
            onCancel={() => setSelected(null)}
          />
          {launch.isError && (
            <p style={{ color: "#ef4444", marginTop: 8 }}>
              Error: {(launch.error as Error).message}
            </p>
          )}
        </div>
      )}
    </div>
  );
};
