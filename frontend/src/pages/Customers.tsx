// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { catalogApi, changeRequestsApi } from "../api/endpoints";
import { useCapabilities } from "../hooks/useCapabilities";
import { useAuth } from "../hooks/useAuth";
import CatalogActionForm from "../components/CatalogActionForm";
import type { CatalogAction } from "../types/api";

interface Customer {
  client_id: string;
  display_name: string;
  plan: string;
  status: string;
  instance_id?: string;
}

export function Customers() {
  const { data: caps } = useCapabilities();
  const { user } = useAuth();
  const navigate = useNavigate();

  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [selectedAction, setSelectedAction] = useState<CatalogAction | null>(null);
  const [inlineResult, setInlineResult] = useState<unknown>(null);

  const { data: customersData, isLoading: customersLoading } = useQuery({
    queryKey: ["customers", "list"],
    queryFn: () => catalogApi.run({ connector_type: "commercial", action_id: "list_customers", params: {} }),
    enabled: caps?.commercial === true && user?.role === "admin",
  });

  const { data: actions = [] } = useQuery<CatalogAction[]>({
    queryKey: ["catalog-actions", "commercial"],
    queryFn: () => catalogApi.actions("commercial"),
    enabled: caps?.commercial === true && user?.role === "admin",
  });

  const runMutation = useMutation({
    mutationFn: (body: Record<string, unknown>) => catalogApi.run(body),
    onSuccess: (data) => setInlineResult(data),
  });

  const crMutation = useMutation({
    mutationFn: (data: Parameters<typeof changeRequestsApi.create>[0]) =>
      changeRequestsApi.create(data),
    onSuccess: (data: { id?: string }) => {
      if (data?.id) navigate(`/change-requests/${data.id}`);
    },
  });

  if (!caps?.commercial || user?.role !== "admin") {
    return (
      <div className="p-8 text-slate-400">
        This page is only available on the commercial edition for admins.
      </div>
    );
  }

  const customers: Customer[] = (customersData as { customers?: Customer[] })?.customers ?? [];

  function handleActionSubmit(action: CatalogAction, params: Record<string, unknown>) {
    if (!selectedCustomer) return;
    const enrichedParams = { client_id: selectedCustomer.client_id, instance_id: selectedCustomer.instance_id, ...params };
    if (action.read_only) {
      runMutation.mutate({ connector_type: action.connector_type, action_id: action.action_id, params: enrichedParams });
    } else {
      crMutation.mutate({
        title: `${action.display_name} — ${selectedCustomer.display_name}`,
        change_type: "catalog_action",
        target_asset_ids: [],
        desired_outcome: { connector_type: action.connector_type, action_id: action.action_id, params: enrichedParams },
      });
    }
  }

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-2xl font-semibold text-white">Customers</h1>

      {customersLoading && <p className="text-slate-400">Loading customers…</p>}

      {customers.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-navy-border">
          <table className="w-full text-sm text-left">
            <thead className="bg-navy-light text-slate-400 uppercase text-xs tracking-wide">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Client ID</th>
                <th className="px-4 py-3">Plan</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-navy-border">
              {customers.map((c) => (
                <tr key={c.client_id} className="hover:bg-navy-light/40 transition-colors">
                  <td className="px-4 py-3 font-medium text-white">{c.display_name}</td>
                  <td className="px-4 py-3 text-slate-400 font-mono text-xs">{c.client_id}</td>
                  <td className="px-4 py-3 text-slate-300 capitalize">{c.plan}</td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${c.status === "active" ? "bg-green-900/40 text-green-400" : "bg-slate-700 text-slate-400"}`}>
                      {c.status}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {actions.length > 0 && (
                      <button
                        onClick={() => { setSelectedCustomer(c); setSelectedAction(actions[0]); setInlineResult(null); }}
                        className="text-xs text-brand-400 hover:text-brand-300 transition-colors"
                      >
                        Actions
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Per-customer action panel */}
      {selectedCustomer && selectedAction && (
        <div className="rounded-lg border border-navy-border bg-navy-light p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-medium text-white">
              {selectedCustomer.display_name} — Actions
            </h2>
            <button onClick={() => { setSelectedCustomer(null); setSelectedAction(null); setInlineResult(null); }} className="text-slate-400 hover:text-white text-xs">
              Close
            </button>
          </div>

          {actions.length > 1 && (
            <div className="flex flex-wrap gap-2">
              {actions.map((a) => (
                <button
                  key={a.action_id}
                  onClick={() => { setSelectedAction(a); setInlineResult(null); }}
                  className={`px-3 py-1 rounded text-xs font-medium transition-colors ${selectedAction.action_id === a.action_id ? "bg-brand-600 text-white" : "bg-navy text-slate-400 hover:text-white border border-navy-border"}`}
                >
                  {a.display_name}
                </button>
              ))}
            </div>
          )}

          <CatalogActionForm
            action={selectedAction}
            initial={{ client_id: selectedCustomer.client_id, instance_id: selectedCustomer.instance_id }}
            onSubmit={(params) => handleActionSubmit(selectedAction, params)}
            submitting={runMutation.isPending || crMutation.isPending}
          />

          {inlineResult != null && (
            <div className="mt-3 rounded bg-navy p-3 border border-navy-border">
              <p className="text-xs font-semibold text-slate-400 mb-1 uppercase">Result</p>
              <pre className="text-xs text-slate-300 overflow-auto max-h-64 whitespace-pre-wrap">
                {JSON.stringify(inlineResult, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
