import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Lightbulb, CheckCircle } from "lucide-react";
import { recommendationsApi, Recommendation } from "../api/endpoints";

const PRIORITY_COLORS: Record<string, string> = {
  critical: "bg-red-500/10 text-red-400 border-red-400/30",
  high: "bg-orange-500/10 text-orange-400 border-orange-400/30",
  medium: "bg-amber-500/10 text-amber-400 border-amber-400/30",
  low: "bg-slate-500/10 text-slate-400 border-slate-400/30",
};

const PRIORITY_TABS = ["all", "critical", "high", "medium", "low"] as const;
type PriorityFilter = (typeof PRIORITY_TABS)[number];

export function RecommendationsPage() {
  const [filter, setFilter] = useState<PriorityFilter>("all");

  const { data: recommendations, isLoading } = useQuery({
    queryKey: ["recommendations"],
    queryFn: recommendationsApi.list,
  });

  const filtered = recommendations?.filter(
    (r) => filter === "all" || r.priority === filter
  ) ?? [];

  const countByPriority = (p: string) =>
    recommendations?.filter((r) => r.priority === p).length ?? 0;

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center gap-3 mb-2">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Lightbulb className="w-5 h-5 text-amber-400" />
        </div>
        <h1 className="text-2xl font-bold text-white">Recommendations</h1>
      </div>
      <p className="text-slate-400 mb-6">Rule-based suggestions to improve your infrastructure posture.</p>

      {/* Priority filter tabs */}
      <div className="flex gap-1 mb-6 flex-wrap">
        {PRIORITY_TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => setFilter(tab)}
            className={`text-xs px-3 py-1.5 rounded-full border transition-colors capitalize ${
              filter === tab
                ? "bg-brand-500/20 border-brand-400/50 text-brand-300"
                : "border-navy-border text-slate-400 hover:text-white hover:border-slate-500"
            }`}
          >
            {tab === "all" ? `All${recommendations ? ` (${recommendations.length})` : ""}` : `${tab} (${countByPriority(tab)})`}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-20 bg-navy-light border border-navy-border rounded-lg animate-pulse" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16">
          <CheckCircle className="w-10 h-10 mx-auto mb-3 text-green-400 opacity-60" />
          <p className="text-slate-400 text-base">
            {filter === "all"
              ? "No recommendations — your infrastructure looks healthy!"
              : `No ${filter} priority recommendations.`}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((rec) => (
            <RecommendationCard key={rec.id} rec={rec} />
          ))}
        </div>
      )}
    </div>
  );
}

function RecommendationCard({ rec }: { rec: Recommendation }) {
  return (
    <div className="bg-navy-light border border-navy-border rounded-lg p-4 flex items-start gap-4">
      <span
        className={`shrink-0 text-xs px-2 py-0.5 rounded border capitalize mt-0.5 ${PRIORITY_COLORS[rec.priority] ?? ""}`}
      >
        {rec.priority}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-slate-200">{rec.title}</p>
        <p className="text-xs text-slate-400 mt-0.5">{rec.description}</p>
      </div>
      <Link
        to={rec.action_link}
        className="shrink-0 text-xs text-brand-400 hover:text-brand-300 underline underline-offset-2"
      >
        {rec.asset_name}
      </Link>
    </div>
  );
}
