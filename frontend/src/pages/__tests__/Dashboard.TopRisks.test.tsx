import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({ data: [] });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 1, asset_count: 3 } });
      if (url.includes("assets")) return Promise.resolve({
        data: [
          { id: "1", name: "prod-web-01", criticality: "critical", environment: "prod", asset_type: "server", tags: [] },
          { id: "2", name: "prod-db-01", criticality: "critical", environment: "prod", asset_type: "database", tags: [] },
          { id: "3", name: "staging-app", criticality: "high", environment: "staging", asset_type: "application", tags: [] },
        ],
      });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows Top Risks heading", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("Top At-Risk Assets")).toBeInTheDocument();
});

test("shows the critical asset names", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("prod-web-01")).toBeInTheDocument();
});
