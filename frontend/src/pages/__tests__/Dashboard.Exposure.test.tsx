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
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 1, asset_count: 5 } });
      if (url.includes("assets")) return Promise.resolve({
        data: [
          { id: "1", criticality: "critical", environment: "prod" },
          { id: "2", criticality: "high", environment: "prod" },
          { id: "3", criticality: "medium", environment: "staging" },
          { id: "4", criticality: "low", environment: "dev" },
          { id: "5", criticality: "critical", environment: "prod" },
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

test("shows Exposure Summary heading", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("Exposure Summary")).toBeInTheDocument();
});

test("shows critical count", async () => {
  wrap(<Dashboard />);
  // 2 critical assets
  expect(await screen.findByText("2")).toBeInTheDocument();
});
