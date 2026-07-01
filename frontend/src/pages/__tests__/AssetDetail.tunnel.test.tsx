// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";

jest.mock("../../lib/env", () => ({
  AGENT_DOWNLOAD_URL: "",
}));

const list = jest.fn().mockResolvedValue([
  { agent_id: "a1", asset_id: "AS1", hostname: "onsite-1", tunnel_enabled: true, tunnel_allowlist: [], online: true },
]);

jest.mock("../../api/endpoints", () => ({
  agentTunnelsApi: { list: () => list() },
  assetsApi: {
    get: jest.fn().mockResolvedValue({ id: "AS1", name: "onsite-1", tags: ["nexplane-agent"], asset_type: "server", criticality: "low", asset_metadata: {}, status: "active" }),
    tags: jest.fn().mockResolvedValue([]),
  },
  changeRequestsApi: {
    list: jest.fn().mockResolvedValue([]),
  },
}));

jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockResolvedValue({ data: [] }),
    post: jest.fn().mockResolvedValue({ data: {} }),
    put: jest.fn().mockResolvedValue({ data: {} }),
    patch: jest.fn().mockResolvedValue({ data: {} }),
    delete: jest.fn().mockResolvedValue({ data: {} }),
  },
}));

jest.mock("../../components/AgentTunnelManager", () => ({
  __esModule: true,
  default: ({ agentId }: { agentId: string }) => <div>tunnel-for-{agentId}</div>,
}));

jest.mock("../../components/RiskBadge", () => ({
  RiskBadge: () => <span>risk</span>,
}));

jest.mock("../../components/StatusBadge", () => ({
  StatusBadge: () => <span>status</span>,
}));

jest.mock("../../components/LoadingSpinner", () => ({
  PageLoading: () => <div>loading</div>,
}));

jest.mock("../../components/IPMigrationWizard", () => ({
  IPMigrationWizard: () => <div>ip-wizard</div>,
}));

jest.mock("../../components/ContainerizationWizard", () => ({
  ContainerizationWizard: () => <div>containerize-wizard</div>,
}));

jest.mock("../../components/MigrateDrawer", () => ({
  MigrateDrawer: () => <div>migrate-drawer</div>,
}));

import { AssetDetail } from "../AssetDetail";

test("shows a Tunnel tab for an agent asset", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/assets/AS1"]}>
        <Routes>
          <Route path="/assets/:id" element={<AssetDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  fireEvent.click(await screen.findByText(/tunnel/i));
  expect(await screen.findByText("tunnel-for-a1")).toBeInTheDocument();
});
