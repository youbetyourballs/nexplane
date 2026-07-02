// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
jest.mock("../../hooks/useCapabilities", () => ({ useCapabilities: () => ({ data: { commercial: true } }) }));
const create = jest.fn().mockResolvedValue({ id: "cr1" });
jest.mock("../../api/endpoints", () => ({
  catalogApi: { run: jest.fn().mockResolvedValue({ customers: [] }),
    actions: jest.fn().mockResolvedValue([{ connector_type: "commercial", action_id: "create_customer", display_name: "Create Customer", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 1, param_schema: [{ name: "client_id", type: "string", required: true }, { name: "display_name", type: "string", required: true }], generic_action: "create_customer", description: "" }]) },
  changeRequestsApi: { create: (...a: unknown[]) => create(...a) },
}));
jest.mock("../../hooks/useAuth", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));
import { Customers } from "../Customers";

test("onboard create_customer creates a catalog_action CR", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Customers /></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole("button", { name: /onboard/i }));
  fireEvent.change(await screen.findByLabelText(/client_id/i), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText(/display_name/i), { target: { value: "Acme" } });
  fireEvent.click(screen.getByRole("button", { name: /run|create|next/i }));
  await waitFor(() => expect(create).toHaveBeenCalled());
  const body = create.mock.calls[0][0];
  expect(body.change_type).toBe("catalog_action");
  expect(body.desired_outcome).toEqual({ connector_type: "commercial", action_id: "create_customer", params: { client_id: "acme", display_name: "Acme" } });
});

test("onboard step 2 labels CR with provision_instance action_id from variables, not stale step state", async () => {
  create.mockReset();
  create.mockResolvedValueOnce({ id: "cr1" }).mockResolvedValueOnce({ id: "cr2" });

  const { catalogApi: mockedCatalogApi } = jest.requireMock("../../api/endpoints") as {
    catalogApi: { actions: jest.Mock };
  };
  mockedCatalogApi.actions.mockResolvedValue([
    { connector_type: "commercial", action_id: "create_customer", display_name: "Create Customer", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 1, param_schema: [{ name: "client_id", type: "string", required: true }, { name: "display_name", type: "string", required: true }], generic_action: "create_customer", description: "" },
    { connector_type: "commercial", action_id: "provision_instance", display_name: "Provision Instance", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 2, param_schema: [{ name: "instance_id", type: "string", required: true }], generic_action: "provision_instance", description: "" },
    { connector_type: "commercial", action_id: "generate_setup_token", display_name: "Generate Setup Token", group: "Customers", domain: "commercial", read_only: false, destructive: false, order: 3, param_schema: [{ name: "instance_id", type: "string", required: true }], generic_action: "generate_setup_token", description: "" },
  ]);

  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><Customers /></MemoryRouter></QueryClientProvider>);

  // Step 1: create_customer
  fireEvent.click(await screen.findByRole("button", { name: /onboard/i }));
  fireEvent.change(await screen.findByLabelText(/client_id/i), { target: { value: "acme" } });
  fireEvent.change(screen.getByLabelText(/display_name/i), { target: { value: "Acme" } });
  fireEvent.click(screen.getByRole("button", { name: /run|create|next/i }));

  // Wait for step 2 form to appear (provision_instance)
  await screen.findByLabelText(/instance_id/i);

  // Step 2: provision_instance
  fireEvent.change(screen.getByLabelText(/instance_id/i), { target: { value: "inst-001" } });
  fireEvent.click(screen.getByRole("button", { name: /run|create|next/i }));

  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));

  const body2 = create.mock.calls[1][0];
  expect(body2.change_type).toBe("catalog_action");
  expect(body2.desired_outcome.action_id).toBe("provision_instance");
});
