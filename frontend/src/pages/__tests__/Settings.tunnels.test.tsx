// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

jest.mock("../../components/AgentTunnelManager", () => ({
  __esModule: true,
  default: () => <div>agent-tunnel-manager</div>,
}));

jest.mock("../../components/ApiTokenManager", () => ({
  ApiTokenManager: () => <div>api-token-manager</div>,
}));

jest.mock("../../components/AgentTokenManager", () => ({
  AgentTokenManager: () => <div>agent-token-manager</div>,
}));

jest.mock("../../components/PageHeader", () => ({
  PageHeader: ({ title }: { title: string }) => <h1>{title}</h1>,
}));

jest.mock("../../components/LoadingSpinner", () => ({
  PageLoading: () => <div>loading</div>,
}));

jest.mock("../../hooks/useAuth", () => ({
  useAuth: () => ({ user: { role: "admin" } }),
}));

jest.mock("../../api/endpoints", () => ({
  settingsApi: {
    get: jest.fn().mockResolvedValue({}),
    updateAIKey: jest.fn(),
    generateAgentSecret: jest.fn(),
  },
}));

jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockResolvedValue({ data: null }),
    put: jest.fn().mockResolvedValue({ data: null }),
  },
}));

import { Settings } from "../Settings";

test("renders the Reverse Tunnels section with AgentTunnelManager", async () => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <Settings />
      </MemoryRouter>
    </QueryClientProvider>
  );
  expect(await screen.findByText(/reverse tunnels/i)).toBeInTheDocument();
  expect(screen.getByText("agent-tunnel-manager")).toBeInTheDocument();
});
