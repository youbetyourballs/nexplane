// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

jest.mock("../../api/endpoints", () => ({
  projectsApi: { list: jest.fn().mockResolvedValue([]) },
}));

import { Projects } from "../Projects";

function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
}

test("empty state mentions AI assistance", async () => {
  wrap(<Projects />);
  expect(await screen.findByText(/Start with AI/i)).toBeInTheDocument();
});
