// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockDraftProject = {
  id: "proj1",
  name: "Test project",
  goal: "Some goal",
  status: "draft",
  members: [],
  ai_context: [],
  created_at: new Date().toISOString(),
};

jest.mock("../../api/endpoints", () => ({
  projectsApi: { get: jest.fn().mockResolvedValue(mockDraftProject), create: jest.fn(), update: jest.fn(), addMember: jest.fn(), removeMember: jest.fn(), updateMember: jest.fn(), aiChat: jest.fn(), getPromptPreview: jest.fn() },
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]), create: jest.fn(), execute: jest.fn(), submitForApproval: jest.fn() },
  assetsApi: { list: jest.fn().mockResolvedValue([]) },
}));

import { ProjectDetail } from "../ProjectDetail";

function wrap(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

test("AI Assistant button is visible and prominent on a draft project", async () => {
  wrap("/projects/proj1");
  const btn = await screen.findByRole("button", { name: /AI Assistant/i });
  expect(btn).toBeInTheDocument();
  expect(btn.className).toMatch(/bg-brand/);
});
