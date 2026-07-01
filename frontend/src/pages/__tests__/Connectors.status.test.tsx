// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockConnectors = [
  { id: "c1", connector_type: "aws", name: "AWS Prod", status: "active", scoped_permissions: {} },
  { id: "c2", connector_type: "okta", name: "Okta", status: "credential_expired", scoped_permissions: {} },
  { id: "c3", connector_type: "azure", name: "Azure", status: "never_synced", scoped_permissions: {} },
  { id: "c4", connector_type: "gcp", name: "GCP", status: "error", scoped_permissions: {} },
  { id: "c5", connector_type: "ldap", name: "LDAP", status: "disabled", scoped_permissions: {} },
];

jest.mock("../../api/endpoints", () => ({
  connectorsApi: {
    list: jest.fn().mockResolvedValue(mockConnectors),
    test: jest.fn(),
    delete: jest.fn(),
    ingest: jest.fn(),
  },
}));
jest.mock("../../api/client", () => ({
  apiClient: { get: jest.fn().mockResolvedValue({ data: null }) },
}));

import { Connectors } from "../Connectors";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows credential expired banner for okta connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/credentials expired/i)).toBeInTheDocument();
});

test("shows error banner for GCP connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/last sync failed/i)).toBeInTheDocument();
});

test("shows never synced label for Azure connector", async () => {
  wrap(<Connectors />);
  const els = await screen.findAllByText(/never synced/i);
  expect(els.length).toBeGreaterThan(0);
});

test("shows disabled label for LDAP connector", async () => {
  wrap(<Connectors />);
  expect(await screen.findByText(/disabled/i)).toBeInTheDocument();
});
