// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import React from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface SLASeverityStats {
  total: number;
  overdue: number;
  due_soon: number;
}

interface SLADashboardResponse {
  critical: SLASeverityStats;
  high: SLASeverityStats;
  medium: SLASeverityStats;
}

const SEVERITY_CONFIG = [
  { key: "critical" as const, label: "Critical", color: "text-red-600", bg: "bg-red-50", border: "border-red-200" },
  { key: "high" as const, label: "High", color: "text-orange-600", bg: "bg-orange-50", border: "border-orange-200" },
  { key: "medium" as const, label: "Medium", color: "text-yellow-600", bg: "bg-yellow-50", border: "border-yellow-200" },
];

export default function SLAWidget() {
  const { data, isLoading } = useQuery<SLADashboardResponse>({
    queryKey: ["sla-dashboard"],
    queryFn: () =>
      apiClient.get<SLADashboardResponse>("/api/v1/vulnerability/sla/dashboard").then((r) => r.data),
    refetchInterval: 300_000, // 5 minutes
  });

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg border p-4">
        <h3 className="text-sm font-semibold mb-3">SLA Status</h3>
        <div className="text-sm text-gray-400">Loading...</div>
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="bg-white rounded-lg border p-4">
      <h3 className="text-sm font-semibold mb-3">SLA Status</h3>
      <div className="space-y-2">
        {SEVERITY_CONFIG.map(({ key, label, color, bg, border }) => {
          const stats = data[key];
          return (
            <div key={key} className={`flex items-center justify-between rounded p-2 border ${bg} ${border}`}>
              <span className={`text-xs font-semibold uppercase ${color}`}>{label}</span>
              <div className="flex gap-3 text-xs text-right">
                <div>
                  <span className="text-red-600 font-bold">{stats.overdue}</span>
                  <span className="text-gray-400 ml-1">overdue</span>
                </div>
                <div>
                  <span className="text-orange-500 font-bold">{stats.due_soon}</span>
                  <span className="text-gray-400 ml-1">due soon</span>
                </div>
                <div>
                  <span className="text-gray-700 font-bold">{stats.total}</span>
                  <span className="text-gray-400 ml-1">total</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <a
        href="/remediation"
        className="block mt-3 text-xs text-blue-600 hover:underline text-right"
      >
        View all findings →
      </a>
    </div>
  );
}
