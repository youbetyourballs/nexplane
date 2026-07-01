// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Plus, FolderOpen, LayoutTemplate, X, Sparkles } from "lucide-react";
import { projectsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { StatusBadge } from "../components/StatusBadge";

const PROJECT_TEMPLATES = [
  {
    id: "cis-hardening",
    name: "CIS Hardening Rollout",
    description: "Apply CIS Level 1 benchmarks across all production servers in rolling batches.",
    icon: "🛡️",
    goal: "Bring all production servers to CIS Level 1 compliance with zero unplanned downtime.",
  },
  {
    id: "patch-campaign",
    name: "Quarterly Patch Campaign",
    description: "Patch all critical and high CVEs across the fleet before the quarterly deadline.",
    icon: "🩹",
    goal: "Remediate all critical CVEs within SLA and high CVEs within 7 days.",
  },
  {
    id: "user-offboarding",
    name: "User Offboarding Sprint",
    description: "Bulk offboard departed users across all connected identity systems.",
    icon: "👤",
    goal: "Revoke access for all departed users within 24 hours of HR notification.",
  },
  {
    id: "key-rotation",
    name: "Credential Rotation",
    description: "Rotate API keys, SSH keys, and service account credentials on a quarterly basis.",
    icon: "🔑",
    goal: "Rotate all long-lived credentials with zero service disruption.",
  },
  {
    id: "dr-validation",
    name: "DR Validation Exercise",
    description: "Run failover drills, verify backup integrity, and document RTO/RPO measurements.",
    icon: "♻️",
    goal: "Validate DR readiness and confirm RTO < 4h and RPO < 1h for all critical services.",
  },
];

function TemplateGallery({ onClose, onSelect }: { onClose: () => void; onSelect: (t: typeof PROJECT_TEMPLATES[0]) => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-slate-900">Project Templates</h2>
          <button onClick={onClose} className="p-1 text-slate-400 hover:text-slate-600 rounded">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="space-y-3">
          {PROJECT_TEMPLATES.map((t) => (
            <button
              key={t.id}
              onClick={() => onSelect(t)}
              className="w-full text-left flex items-start gap-4 p-4 rounded-lg border border-slate-200 hover:border-brand-400 hover:bg-brand-50 transition-colors"
            >
              <span className="text-2xl shrink-0">{t.icon}</span>
              <div>
                <p className="text-sm font-semibold text-slate-900">{t.name}</p>
                <p className="text-xs text-slate-500 mt-0.5">{t.description}</p>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

export function Projects() {
  const navigate = useNavigate();
  const [showTemplates, setShowTemplates] = useState(false);

  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => projectsApi.list(),
  });

  if (isLoading) return <PageLoading />;

  function handleTemplateSelect(t: typeof PROJECT_TEMPLATES[0]) {
    setShowTemplates(false);
    navigate(`/projects/new?name=${encodeURIComponent(t.name)}&goal=${encodeURIComponent(t.goal)}`);
  }

  return (
    <div className="p-8">
      <PageHeader
        title="Projects"
        subtitle={`${projects?.length ?? 0} projects`}
        actions={
          <div className="flex gap-2">
            <button
              onClick={() => setShowTemplates(true)}
              className="inline-flex items-center gap-1.5 px-3 py-2 border border-slate-200 text-slate-700 text-sm font-medium rounded-md hover:bg-slate-50"
            >
              <LayoutTemplate className="w-4 h-4" />
              New from template
            </button>
            <button
              onClick={() => navigate("/projects/new")}
              className="inline-flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
            >
              <Plus className="w-4 h-4" />
              New Project
            </button>
          </div>
        }
      />

      {showTemplates && (
        <TemplateGallery onClose={() => setShowTemplates(false)} onSelect={handleTemplateSelect} />
      )}

      {projects?.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center max-w-sm mx-auto">
          <FolderOpen className="w-12 h-12 text-slate-300 mb-4" />
          <p className="text-slate-700 text-sm font-medium">No projects yet</p>
          <p className="text-slate-400 text-xs mt-1 mb-6">
            Describe a goal to the AI and it will propose the change requests needed to achieve it.
          </p>
          <button
            onClick={() => navigate("/projects/new")}
            className="inline-flex items-center gap-2 px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            <Sparkles className="w-4 h-4" />
            Start with AI
          </button>
          <div className="flex gap-3 mt-4">
            <button
              onClick={() => setShowTemplates(true)}
              className="text-slate-500 text-xs hover:text-slate-700 hover:underline"
            >
              Use a template
            </button>
            <span className="text-slate-300 text-xs">·</span>
            <button
              onClick={() => navigate("/projects/new")}
              className="text-slate-500 text-xs hover:text-slate-700 hover:underline"
            >
              Blank project
            </button>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {(projects ?? []).map((project) => {
            const pct =
              project.member_count > 0
                ? Math.round((project.completed_count / project.member_count) * 100)
                : 0;
            return (
              <button
                key={project.id}
                onClick={() => navigate(`/projects/${project.id}`)}
                className="text-left bg-white border border-slate-200 rounded-lg p-5 hover:border-brand-300 transition-colors"
              >
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-sm font-semibold text-slate-900 truncate pr-2">
                    {project.name}
                  </h3>
                  <StatusBadge status={project.status} size="sm" />
                </div>
                {project.goal && (
                  <p className="text-xs text-slate-500 mb-3 line-clamp-2">{project.goal}</p>
                )}
                <div className="mt-auto">
                  <div className="flex justify-between text-xs text-slate-400 mb-1">
                    <span>{project.completed_count} / {project.member_count} change requests</span>
                    <span>{pct}%</span>
                  </div>
                  <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-brand-500 rounded-full transition-all"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="text-xs text-slate-400 mt-2">
                    {new Date(project.created_at).toLocaleDateString()}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
