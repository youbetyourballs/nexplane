import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  assetsApi: {
    list: jest.fn().mockResolvedValue([]),
    tags: jest.fn().mockResolvedValue([]),
    create: jest.fn(),
    delete: jest.fn(),
    bulkTag: jest.fn(),
  },
  connectorsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({
  apiClient: { post: jest.fn() },
}));

import { Assets } from "../Assets";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows Unmitigated Criticals preset button", async () => {
  wrap(<Assets />);
  expect(await screen.findByText("Unmitigated Criticals")).toBeInTheDocument();
});

test("shows Public-Facing High+ preset button", async () => {
  wrap(<Assets />);
  expect(await screen.findByText("Public-Facing High+")).toBeInTheDocument();
});

test("clicking Unmitigated Criticals applies criticality filter", async () => {
  wrap(<Assets />);
  const btn = await screen.findByText("Unmitigated Criticals");
  fireEvent.click(btn);
  const input = screen.getByPlaceholderText(/search assets/i) as HTMLInputElement;
  expect(input.value).toContain("critical");
});
