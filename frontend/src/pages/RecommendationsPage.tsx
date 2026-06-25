import { Lightbulb, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export function RecommendationsPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Lightbulb className="w-6 h-6 text-amber-400" />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-2 py-1">
          Preview
        </span>
      </div>

      <h1 className="text-2xl font-bold text-white mb-3">Recommendations</h1>
      <p className="text-lg text-slate-400 mb-2 font-medium">What should I fix next?</p>

      <p className="text-slate-400 mb-6 leading-relaxed">
        The Recommendation Engine will continuously analyze your asset inventory, open findings,
        change history, and connector data to surface prioritized, actionable improvement
        suggestions — think Dependabot for infrastructure. Each recommendation will include the
        affected assets, severity, confidence score, and a one-click path to creating the
        remediation change request.
      </p>

      <div className="bg-navy-light border border-navy-border rounded-lg p-5 mb-6">
        <p className="text-sm font-semibold text-slate-300 mb-3">Example recommendations:</p>
        <ul className="space-y-2 text-sm text-slate-400">
          {[
            "Remove stale firewall rules with no recent traffic",
            "Rotate secrets older than 90 days",
            "Remove unused IAM permissions",
            "Archive inactive user accounts",
            "Add owners to assets missing ownership",
            "Improve rollback coverage for high-risk change types",
            "Convert high-frequency manual runbooks into executable workflows",
          ].map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              {item}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        In the meantime, use Findings & Remediation to work through open security findings on your assets.
      </p>
      <Link
        to="/remediation"
        className="inline-flex items-center gap-2 text-sm font-medium text-brand-400 hover:text-brand-300 transition-colors"
      >
        View findings <ArrowRight className="w-4 h-4" />
      </Link>
    </div>
  );
}
