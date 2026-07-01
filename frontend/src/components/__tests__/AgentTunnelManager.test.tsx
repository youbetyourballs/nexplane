import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../hooks/useAuth", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));
const list = jest.fn();
const set = jest.fn();
jest.mock("../../api/endpoints", () => ({ agentTunnelsApi: { list: () => list(), set: (...a: unknown[]) => set(...a) } }));

import AgentTunnelManager from "../AgentTunnelManager";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
const AGENT = { agent_id: "a1", asset_id: "as1", hostname: "onsite-1", tunnel_enabled: false, tunnel_allowlist: [], online: true };

afterEach(() => { list.mockReset(); set.mockReset(); });

test("shows agents with online badge", async () => {
  list.mockResolvedValue([AGENT]);
  wrap(<AgentTunnelManager />);
  expect(await screen.findByText("onsite-1")).toBeInTheDocument();
  expect(screen.getByText(/online/i)).toBeInTheDocument();
});

test("enabling with empty allowlist pre-fills RFC1918", async () => {
  list.mockResolvedValue([AGENT]);
  set.mockResolvedValue({ ...AGENT, tunnel_enabled: true, tunnel_allowlist: ["10.0.0.0/8:*", "172.16.0.0/12:*", "192.168.0.0/16:*"] });
  wrap(<AgentTunnelManager />);
  fireEvent.click(await screen.findByLabelText(/enable tunnel/i));
  await waitFor(() => expect(set).toHaveBeenCalled());
  const [, body] = set.mock.calls[0];
  expect(body.enabled).toBe(true);
  expect(body.allowlist).toEqual(["10.0.0.0/8:*", "172.16.0.0/12:*", "192.168.0.0/16:*"]);
});

test("rejects an invalid allowlist entry", async () => {
  list.mockResolvedValue([{ ...AGENT, tunnel_enabled: true, tunnel_allowlist: ["10.0.0.0/8:*"] }]);
  wrap(<AgentTunnelManager />);
  fireEvent.change(await screen.findByPlaceholderText(/add destination/i), { target: { value: "bad:99999" } });
  fireEvent.click(screen.getByText(/^add$/i));
  expect(await screen.findByText(/invalid port/i)).toBeInTheDocument();
});
