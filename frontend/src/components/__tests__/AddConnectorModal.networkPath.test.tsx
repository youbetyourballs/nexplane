// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const list = jest.fn().mockResolvedValue([
  { agent_id: "a1", asset_id: null, hostname: "onsite-1", tunnel_enabled: true, tunnel_allowlist: ["10.0.0.0/8:*"], online: true },
]);
const create = jest.fn().mockResolvedValue({ id: "c1" });

jest.mock("../../api/endpoints", () => ({
  agentTunnelsApi: { list: () => list() },
  connectorsApi: { create: (...a: unknown[]) => create(...a) },
}));

import AddConnectorModal from "../AddConnectorModal";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

afterEach(() => { list.mockClear(); create.mockClear(); });

test("shows Via agent control for a routable type and hides it for a non-routable type", async () => {
  wrap(
    <AddConnectorModal
      token="tok"
      onClose={() => {}}
      onCreated={() => {}}
    />
  );

  // Select a routable type (postgres)
  fireEvent.change(screen.getByLabelText(/connector type/i), { target: { value: "postgres" } });

  // "Via agent" radio should appear
  const viaAgentRadio = await screen.findByLabelText(/via agent/i);
  expect(viaAgentRadio).toBeInTheDocument();

  // Click "Via agent" to show agent dropdown
  fireEvent.click(viaAgentRadio);

  // Agent dropdown should show "onsite-1"
  expect(await screen.findByText("onsite-1")).toBeInTheDocument();

  // Skip TLS checkbox should be present
  expect(screen.getByLabelText(/skip tls host verification/i)).toBeInTheDocument();

  // Switch to a non-routable type (aws) -> control should disappear
  fireEvent.change(screen.getByLabelText(/connector type/i), { target: { value: "aws" } });
  expect(screen.queryByLabelText(/via agent/i)).toBeNull();
});

test("submits network_path via_agent when agent is selected", async () => {
  const onCreated = jest.fn();
  wrap(
    <AddConnectorModal
      token="tok"
      onClose={() => {}}
      onCreated={onCreated}
    />
  );

  // Select postgres (routable)
  fireEvent.change(screen.getByLabelText(/connector type/i), { target: { value: "postgres" } });

  // Click Via agent
  fireEvent.click(await screen.findByLabelText(/via agent/i));

  // Wait for agent dropdown and select agent
  const agentOption = await screen.findByText("onsite-1");
  fireEvent.change(agentOption.closest("select")!, { target: { value: "a1" } });

  // Click Add Connector
  fireEvent.click(screen.getByRole("button", { name: /add connector/i }));

  await waitFor(() => expect(create).toHaveBeenCalled());
  const payload = create.mock.calls[0][0];
  expect(payload.network_path).toBe("via_agent:a1");
  expect(payload.network_tls_skip_verify).toBe(false);
});
