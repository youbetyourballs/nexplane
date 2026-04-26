import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Plus, FolderOpen } from "lucide-react";
import { projectsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import { StatusBadge } from "../components/StatusBadge";

export function Projects() {
  const navigate = useNavigate();

  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => projectsApi.list(),
  });

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8">
      <PageHeader
        title="Projects"
        subtitle={`${projects?.length ?? 0} projects`}
        actions={
          <button
            onClick={() => navigate("/projects/new")}
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700"
          >
            <Plus className="w-4 h-4" />
            New Project
          </button>
        }
      />

      {projects?.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <FolderOpen className="w-12 h-12 text-slate-300 mb-4" />
          <p className="text-slate-500 text-sm">No projects yet.</p>
          <button
            onClick={() => navigate("/projects/new")}
            className="mt-4 text-brand-600 text-sm hover:underline"
          >
            Create your first project
          </button>
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
