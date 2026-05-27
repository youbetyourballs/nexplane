import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockProject = {
  id: "proj1",
  name: "Harden prod servers",
  goal: "Apply CIS benchmarks",
  status: "draft",
  members: [],
  ai_context: [],
  created_at: new Date().toISOString(),
};

jest.mock("../../api/endpoints", () => ({
  projectsApi: {
    get: jest.fn().mockResolvedValue(mockProject),
    create: jest.fn(),
    update: jest.fn(),
    addMember: jest.fn(),
    removeMember: jest.fn(),
    updateMember: jest.fn(),
    aiChat: jest.fn(),
    getPromptPreview: jest.fn(),
  },
  changeRequestsApi: { list: jest.fn().mockResolvedValue([]), create: jest.fn(), execute: jest.fn(), submitForApproval: jest.fn() },
  assetsApi: { list: jest.fn().mockResolvedValue([]) },
}));

// jsdom doesn't implement scrollIntoView
window.HTMLElement.prototype.scrollIntoView = jest.fn();

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

test("AI panel is open when URL has showAI=1", async () => {
  wrap("/projects/proj1?showAI=1");
  expect(await screen.findByText("✦ AI Assistant")).toBeInTheDocument();
});

test("AI panel is closed when URL has no showAI param", async () => {
  wrap("/projects/proj1");
  expect(await screen.findByText("Harden prod servers")).toBeInTheDocument();
  expect(screen.queryByText("✦ AI Assistant")).not.toBeInTheDocument();
});
