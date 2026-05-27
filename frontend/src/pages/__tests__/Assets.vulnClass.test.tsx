import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  assetsApi: { list: jest.fn().mockResolvedValue([]), tags: jest.fn().mockResolvedValue([]), create: jest.fn(), delete: jest.fn(), bulkTag: jest.fn() },
  connectorsApi: { list: jest.fn().mockResolvedValue([]) },
}));
jest.mock("../../api/client", () => ({ apiClient: { post: jest.fn() } }));

import { Assets } from "../Assets";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("shows vuln class dropdown", () => {
  wrap(<Assets />);
  expect(screen.getByRole("combobox", { name: /vuln class/i })).toBeInTheDocument();
});

test("vuln class dropdown has RCE option", () => {
  wrap(<Assets />);
  const select = screen.getByRole("combobox", { name: /vuln class/i });
  expect(select).toContainHTML("rce");
});
