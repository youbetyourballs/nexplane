import { Zap, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";

export function ImpactSimulationPage() {
  return (
    <div className="max-w-2xl mx-auto py-16 px-6">
      <div className="flex items-center gap-3 mb-6">
        <div className="p-2 bg-amber-500/10 rounded-lg">
          <Zap className="w-6 h-6 text-amber-400" />
        </div>
        <span className="text-xs font-semibold uppercase tracking-wide text-amber-400 border border-amber-400/40 rounded px-2 py-1">
          Preview
        </span>
      </div>

      <h1 className="text-2xl font-bold text-white mb-3">Impact Simulation</h1>
      <p className="text-lg text-slate-400 mb-2 font-medium">What will happen if I change it?</p>

      <p className="text-slate-400 mb-6 leading-relaxed">
        Impact Simulation will let you model infrastructure changes before executing them. Select an
        asset, choose the change you're considering, and the platform will traverse the asset graph
        to show you: which systems depend on this asset, what will break, who will be affected, the
        blast radius, and the recommended rollback strategy — all before a single byte changes in
        production.
      </p>

      <div className="bg-navy-light border border-navy-border rounded-lg p-5 mb-6">
        <p className="text-sm font-semibold text-slate-300 mb-3">What you'll be able to simulate:</p>
        <ul className="space-y-2 text-sm text-slate-400">
          {[
            "Remove or restrict a firewall rule",
            "Rotate a certificate or secret",
            "Change a DNS record",
            "Decommission a VM or instance",
            "Restrict an IAM permission",
            "Remove public exposure",
            "Restart or migrate a service",
          ].map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              {item}
            </li>
          ))}
        </ul>
      </div>

      <p className="text-sm text-slate-500 mb-4">
        In the meantime, use Asset details to explore an asset's connections and open findings
        before creating a change request.
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
