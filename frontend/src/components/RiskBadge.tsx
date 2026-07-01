// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import clsx from "clsx";
import type { RiskLevel, Criticality } from "../types/api";

const RISK_STYLES: Record<string, string> = {
  low: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border border-amber-200",
  high: "bg-orange-50 text-orange-700 border border-orange-200",
  critical: "bg-red-50 text-red-700 border border-red-200",
};

interface Props {
  level: RiskLevel | Criticality;
  size?: "sm" | "md";
}

export function RiskBadge({ level, size = "md" }: Props) {
  const style = RISK_STYLES[level] ?? "bg-slate-100 text-slate-600";
  const label = level.charAt(0).toUpperCase() + level.slice(1);
  return (
    <span
      className={clsx(
        "inline-flex items-center font-medium rounded",
        size === "sm" ? "px-1.5 py-0.5 text-xs" : "px-2 py-0.5 text-xs",
        style
      )}
    >
      {label}
    </span>
  );
}
