import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  changeRequestsApi: {
    list: jest.fn().mockResolvedValue([
      {
        id: "cr1",
        title: "Patch web servers",
        change_type: "patch_packages",
        status: "executing",
        risk_level: "high",
        requester: { name: "Alice" },
        created_at: new Date().toISOString(),
        rollback_available: true,
      },
    ]),
  },
}));
jest.mock("../../api/client", () => ({
  apiClient: {
    get: jest.fn().mockImplementation((url: string) => {
      if (url.includes("connectors")) return Promise.resolve({ data: [] });
      if (url.includes("onboarding")) return Promise.resolve({ data: { steps: [], connector_count: 1, asset_count: 1 } });
      if (url.includes("assets")) return Promise.resolve({ data: [] });
      return Promise.resolve({ data: [] });
    }),
  },
}));

import { Dashboard } from "../Dashboard";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

test("shows In-Flight section header", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText("In-Flight Changes")).toBeInTheDocument();
});

test("shows rollback available indicator for executing CRs", async () => {
  wrap(<Dashboard />);
  expect(await screen.findByText(/rollback available/i)).toBeInTheDocument();
});
