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
