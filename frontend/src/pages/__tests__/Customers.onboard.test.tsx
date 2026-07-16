// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
jest.mock("../../hooks/useCapabilities", () => ({ useCapabilities: () => ({ data: { commercial: true } }) }));
const create = jest.fn().mockResolvedValue({ id: "cr1" });
jest.mock("../../api/endpoints", () => ({
  catalogApi: { run: jest.fn().mockResolvedValue({ customers: [] }),
    actions: jest.fn().mockResolvedValue([
      { connector_type: "commercial", action_id: "create_customer", display_name: "Create Customer", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 1, param_schema: [{ name: "client_id", type: "string", required: true }, { name: "display_name", type: "string", required: true }], generic_action: "create_customer", description: "" },
    ]) },
  changeRequestsApi: { create: (...a: unknown[]) => create(...a) },
}));
jest.mock("../../hooks/useAuth", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));
import { Customers } from "../Customers";

test("onboard wizard submits a single catalog_workflow CR when all steps collected", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Customers /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /onboard/i }));
  fireEvent.change(await screen.findByLabelText(/client_id/i), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText(/display_name/i), { target: { value: "Acme" } });
  fireEvent.click(screen.getByRole("button", { name: /run|create|next/i }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  const body = create.mock.calls[0][0];
  expect(body.change_type).toBe("catalog_workflow");
  expect(body.desired_outcome.steps).toHaveLength(1);
  expect(body.desired_outcome.steps[0]).toEqual({
    connector_type: "commercial",
    action_id: "create_customer",
    params: { client_id: "acme", display_name: "Acme" },
  });
});

test("onboard wizard collects params step-by-step and submits one CR after the final step", async () => {
  create.mockReset();
  create.mockResolvedValueOnce({ id: "cr1" });

  const { catalogApi: mockedCatalogApi } = jest.requireMock("../../api/endpoints") as {
    catalogApi: { actions: jest.Mock };
  };
  mockedCatalogApi.actions.mockResolvedValue([
    { connector_type: "commercial", action_id: "create_customer", display_name: "Create Customer", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 1, param_schema: [{ name: "client_id", type: "string", required: true }, { name: "display_name", type: "string", required: true }], generic_action: "create_customer", description: "" },
    { connector_type: "commercial", action_id: "provision_instance", display_name: "Provision Instance", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 2, param_schema: [{ name: "mode", type: "string", required: true }], generic_action: "provision_instance", description: "" },
  ]);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Customers /></MemoryRouter></QueryClientProvider>);

  // Step 1: create_customer — no CR submitted yet
  fireEvent.click(await screen.findByRole("button", { name: /onboard/i }));
  fireEvent.change(await screen.findByLabelText(/client_id/i), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText(/display_name/i), { target: { value: "Acme" } });
  fireEvent.click(screen.getByRole("button", { name: /run|create|next/i }));

  // Wizard advances — step 2 form appears; create not yet called
  await screen.findByLabelText(/mode/i);
  expect(create).not.toHaveBeenCalled();

  // Step 2: provision_instance — submits the CR
  fireEvent.change(screen.getByLabelText(/mode/i), { target: { value: "managed" } });
  fireEvent.click(screen.getByRole("button", { name: /run|create|next/i }));

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  const body = create.mock.calls[0][0];
  expect(body.change_type).toBe("catalog_workflow");
  expect(body.desired_outcome.steps).toHaveLength(2);
  expect(body.desired_outcome.steps[0].action_id).toBe("create_customer");
  expect(body.desired_outcome.steps[1].action_id).toBe("provision_instance");
});
