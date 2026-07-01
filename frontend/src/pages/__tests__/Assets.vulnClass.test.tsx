// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

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

test("shows vuln class dropdown", async () => {
  wrap(<Assets />);
  expect(await screen.findByRole("combobox", { name: /vuln class/i })).toBeInTheDocument();
});

test("vuln class dropdown has RCE option", async () => {
  wrap(<Assets />);
  const select = await screen.findByRole("combobox", { name: /vuln class/i });
  expect(select).toContainHTML("rce");
});
