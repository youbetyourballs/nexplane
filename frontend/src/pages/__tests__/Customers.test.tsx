// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
jest.mock("../../hooks/useCapabilities", () => ({ useCapabilities: () => ({ data: { commercial: true, edition: "commercial", domains: ["commercial"] } }) }));
const run = jest.fn().mockResolvedValue({ customers: [{ client_id: "acme", display_name: "Acme Corp", plan: "team", status: "active" }] });
const actions = jest.fn().mockResolvedValue([]);
jest.mock("../../api/endpoints", () => ({ catalogApi: { run: (...a: unknown[]) => run(...a), actions: () => actions() }, changeRequestsApi: { create: jest.fn() } }));
jest.mock("../../hooks/useAuth", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));
import { Customers } from "../Customers";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("lists customers from list_customers", async () => {
  wrap(<Customers />);
  expect(await screen.findByText("Acme Corp")).toBeInTheDocument();
  expect(run).toHaveBeenCalled();
});
