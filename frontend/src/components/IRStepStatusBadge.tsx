import React from "react";
import { StepResult } from "../api/ir";

interface IRStepStatusBadgeProps {
  stepResults: Record<string, StepResult>;
}

const statusIcon: Record<string, { icon: string; color: string; label: string }> = {
  completed: { icon: "✓", color: "#22c55e", label: "Completed" },
  failed:    { icon: "✗", color: "#ef4444", label: "Failed" },
  skipped:   { icon: "—", color: "#94a3b8", label: "Skipped" },
};

export const IRStepStatusBadge: React.FC<IRStepStatusBadgeProps> = ({ stepResults }) => {
  const entries = Object.entries(stepResults);
  if (entries.length === 0) {
    return <span style={{ color: "#94a3b8", fontSize: 12 }}>No steps recorded</span>;
  }

  return (
    <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      {entries.map(([name, result]) => {
        const s = statusIcon[result.status] ?? { icon: "?", color: "#cbd5e1", label: result.status };
        return (
          <span
            key={name}
            title={`${name}: ${s.label}${result.error ? ` — ${result.error}` : ""}`}
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 22,
              height: 22,
              borderRadius: "50%",
              background: s.color,
              color: "#fff",
              fontSize: 12,
              fontWeight: "bold",
              cursor: "default",
            }}
          >
            {s.icon}
          </span>
        );
      })}
    </div>
  );
};
