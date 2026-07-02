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

const ONBOARD_STEPS: string[] = ["create_customer", "provision_instance", "generate_setup_token"];

export function Customers() {
  const { data: caps } = useCapabilities();
  const { user } = useAuth();
  const navigate = useNavigate();

  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null);
  const [selectedAction, setSelectedAction] = useState<CatalogAction | null>(null);
  const [inlineResult, setInlineResult] = useState<unknown>(null);

  // Onboard wizard state
  const [onboardOpen, setOnboardOpen] = useState(false);
  const [onboardStep, setOnboardStep] = useState(0);
  const [onboardCRs, setOnboardCRs] = useState<Array<{ action_id: string; cr_id: string }>>([]);

  // Destructive typed-confirm state
  const [destructiveAction, setDestructiveAction] = useState<{ action: CatalogAction; customer: Customer } | null>(null);
  const [confirmInput, setConfirmInput] = useState("");

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

  const onboardCRMutation = useMutation({
    mutationFn: (data: Parameters<typeof changeRequestsApi.create>[0]) =>
      changeRequestsApi.create(data),
    onSuccess: (data: { id?: string }, variables: Parameters<typeof changeRequestsApi.create>[0]) => {
      const stepActionId = (variables?.desired_outcome as { action_id?: string })?.action_id ?? "";
      setOnboardCRs((prev) => [...prev, { action_id: String(stepActionId), cr_id: data?.id ?? "" }]);
      setOnboardStep((s) => (s < onboardStepActions.length - 1 ? s + 1 : s));
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

  // Compute onboard step actions from loaded catalog actions
  const onboardStepActions: CatalogAction[] = ONBOARD_STEPS
    .map((id) => actions.find((a) => a.action_id === id))
    .filter((a): a is CatalogAction => a !== undefined);

  function handleActionSubmit(action: CatalogAction, params: Record<string, unknown>) {
    if (!selectedCustomer) return;
    const enrichedParams = { client_id: selectedCustomer.client_id, instance_id: selectedCustomer.instance_id, ...params };
    if (action.read_only) {
      runMutation.mutate({ connector_type: action.connector_type, action_id: action.action_id, params: enrichedParams });
    } else if (action.destructive) {
      setDestructiveAction({ action, customer: selectedCustomer });
      setConfirmInput("");
    } else {
      crMutation.mutate({
        title: `${action.display_name} — ${selectedCustomer.display_name}`,
        change_type: "catalog_action",
        target_asset_ids: [],
        desired_outcome: { connector_type: action.connector_type, action_id: action.action_id, params: enrichedParams },
      });
    }
  }

  function handleOnboardSubmit(action: CatalogAction, params: Record<string, unknown>) {
    onboardCRMutation.mutate({
      title: `${action.display_name} — Onboard`,
      change_type: "catalog_action",
      target_asset_ids: [],
      desired_outcome: { connector_type: action.connector_type, action_id: action.action_id, params },
    });
  }

  function handleDestructiveConfirm() {
    if (!destructiveAction) return;
    const { action, customer } = destructiveAction;
    const enrichedParams = { client_id: customer.client_id, instance_id: customer.instance_id };
    crMutation.mutate({
      title: `${action.display_name} — ${customer.display_name}`,
      change_type: "catalog_action",
      target_asset_ids: [],
      desired_outcome: { connector_type: action.connector_type, action_id: action.action_id, params: enrichedParams },
    });
    setDestructiveAction(null);
    setConfirmInput("");
  }

  const currentOnboardAction = onboardStepActions[onboardStep];

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-white">Customers</h1>
        <button
          onClick={() => { setOnboardOpen(true); setOnboardStep(0); setOnboardCRs([]); }}
          className="px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded hover:bg-brand-700 transition-colors"
        >
          Onboard Customer
        </button>
      </div>

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

      {/* Onboard wizard modal */}
      {onboardOpen && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-navy rounded-xl border border-navy-border p-6 w-full max-w-lg space-y-4 shadow-xl">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">
                Onboard Customer — Step {onboardStep + 1} of {onboardStepActions.length || ONBOARD_STEPS.length}
              </h2>
              <button onClick={() => setOnboardOpen(false)} className="text-slate-400 hover:text-white text-xs">
                Cancel
              </button>
            </div>

            {/* Completed steps */}
            {onboardCRs.length > 0 && (
              <div className="space-y-1">
                {onboardCRs.map((cr) => (
                  <p key={cr.action_id} className="text-xs text-green-400">
                    ✓ {cr.action_id} — CR: {cr.cr_id}
                  </p>
                ))}
              </div>
            )}

            {currentOnboardAction ? (
              <CatalogActionForm
                action={currentOnboardAction}
                initial={{}}
                onSubmit={(params) => handleOnboardSubmit(currentOnboardAction, params)}
                submitting={onboardCRMutation.isPending}
              />
            ) : (
              <p className="text-slate-400 text-sm">
                {onboardCRs.length === ONBOARD_STEPS.length
                  ? "Onboarding complete!"
                  : "Loading wizard steps…"}
              </p>
            )}

            {onboardCRs.length === onboardStepActions.length && onboardStepActions.length > 0 && (
              <button
                onClick={() => setOnboardOpen(false)}
                className="w-full px-4 py-2 bg-green-700 text-white text-sm rounded hover:bg-green-600"
              >
                Done
              </button>
            )}
          </div>
        </div>
      )}

      {/* Destructive typed-confirm modal */}
      {destructiveAction && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-navy rounded-xl border border-red-700 p-6 w-full max-w-md space-y-4 shadow-xl">
            <h2 className="text-lg font-semibold text-red-400">Confirm Destructive Action</h2>
            <p className="text-sm text-slate-300">
              You are about to run <strong>{destructiveAction.action.display_name}</strong> on customer{" "}
              <strong>{destructiveAction.customer.display_name}</strong>. This action is destructive and cannot be undone.
            </p>
            <p className="text-sm text-slate-400">
              Type <span className="font-mono text-white">{destructiveAction.customer.client_id}</span> to confirm:
            </p>
            <input
              type="text"
              value={confirmInput}
              onChange={(e) => setConfirmInput(e.target.value)}
              className="w-full border border-slate-600 rounded px-3 py-2 text-sm bg-navy-light text-white"
              placeholder={destructiveAction.customer.client_id}
              aria-label="Confirm client ID"
            />
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => { setDestructiveAction(null); setConfirmInput(""); }}
                className="px-4 py-2 text-sm text-slate-400 hover:text-white rounded border border-navy-border"
              >
                Cancel
              </button>
              <button
                onClick={handleDestructiveConfirm}
                disabled={confirmInput !== destructiveAction.customer.client_id || crMutation.isPending}
                className="px-4 py-2 text-sm bg-red-700 text-white rounded hover:bg-red-600 disabled:opacity-40"
              >
                {crMutation.isPending ? "Submitting…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
