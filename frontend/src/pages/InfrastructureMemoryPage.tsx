import { Brain, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export function InfrastructureMemoryPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Brain className="w-6 h-6 text-amber-400" />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-2 py-1">
          Preview
        </span>
      </div>

      <h1 className="text-2xl font-bold text-white mb-3">Infrastructure Memory</h1>
      <p className="text-lg text-slate-400 mb-2 font-medium">Why does this exist?</p>

      <p className="text-slate-400 mb-6 leading-relaxed">
        Infrastructure Memory will preserve the institutional knowledge behind every asset and
        configuration in your environment. Instead of asking a colleague why TCP 8443 is open or
        who approved that firewall rule three years ago, you'll be able to query the platform
        directly — and get a sourced, auditable answer linked to the original change request,
        approval, and business justification.
      </p>

      <div className="bg-navy-light border border-navy-border rounded-lg p-5 mb-6">
        <p className="text-sm font-semibold text-slate-300 mb-3">Questions you'll be able to answer:</p>
        <ul className="space-y-2 text-sm text-slate-400">
          {[
            "Why is TCP 8443 open on this host?",
            "Who approved this firewall rule?",
            "Why does this DNS record exist?",
            "What system owns this certificate?",
            "What change introduced this IAM permission?",
            "Is this asset still in use?",
            "Can this be safely removed?",
          ].map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              {item}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        In the meantime, use Asset details to view the change timeline for any asset.
      </p>
      <Link
        to="/assets"
        className="inline-flex items-center gap-2 text-sm font-medium text-brand-400 hover:text-brand-300 transition-colors"
      >
        Browse assets <ArrowRight className="w-4 h-4" />
      </Link>
    </div>
  );
}
