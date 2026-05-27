import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({
        data: [
          { id: "c1", connector_type: "aws", name: "AWS Prod", status: "error" },
          { id: "c2", connector_type: "okta", name: "Okta", status: "credential_expired" },
        ],
      });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 2, asset_count: 5 } });
      if (url.includes("assets")) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows connector warning banner when connectors have errors", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText(/connector.*issue/i)).toBeInTheDocument();
});

test("mentions the number of failing connectors", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText(/2/)).toBeInTheDocument();
});
