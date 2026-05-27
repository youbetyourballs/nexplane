import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "../Sidebar";

// Mock useAuth
jest.mock("../../hooks/useAuth", () => ({
  useAuth: () => ({ user: { name: "Test", email: "t@t.com", role: "admin" }, logout: jest.fn() }),
}));

// Mock apiClient
jest.mock("../../api/client", () => ({
  apiClient: { get: jest.fn().mockResolvedValue({ data: [] }) },
}));

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
}

test("shows Operations section with Runbooks, Scheduled Ops, Maintenance Windows", () => {
  wrap(<Sidebar />);
  expect(screen.getByText("Operations")).toBeInTheDocument();
  expect(screen.getByText("Runbooks")).toBeInTheDocument();
  expect(screen.getByText("Scheduled Ops")).toBeInTheDocument();
  expect(screen.getByText("Maintenance Windows")).toBeInTheDocument();
});

test("does not show Vuln Remediation as a top-level nav item", () => {
  wrap(<Sidebar />);
  expect(screen.queryByText("Vuln Remediation")).not.toBeInTheDocument();
});

test("does not show Approvals Queue as a top-level nav item", () => {
  wrap(<Sidebar />);
  expect(screen.queryByText("Approvals Queue")).not.toBeInTheDocument();
});

test("shows Change Requests nav item", () => {
  wrap(<Sidebar />);
  expect(screen.getByText("Change Requests")).toBeInTheDocument();
});

test("shows pending approval count badge on Change Requests when there are pending approvals", async () => {
  const { apiClient } = require("../../api/client");
  apiClient.get.mockImplementation((url: string) => {
    if (url.includes("change-requests")) {
      return Promise.resolve({ data: [{ id: "cr1" }, { id: "cr2" }, { id: "cr3" }] });
    }
    return Promise.resolve({ data: [] });
  });
  wrap(<Sidebar />);
  expect(await screen.findByText("3")).toBeInTheDocument();
});

test("Operations section items are hidden when section is collapsed", () => {
  const { fireEvent } = require("@testing-library/react");
  wrap(<Sidebar />);
  const opsButton = screen.getByRole("button", { name: /operations/i });
  fireEvent.click(opsButton);
  expect(screen.queryByText("Runbooks")).not.toBeInTheDocument();
});
