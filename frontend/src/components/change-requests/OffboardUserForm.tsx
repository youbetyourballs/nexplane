import type { ChangeEvent } from "react";

export interface OffboardUserPayload {
  target_email: string;
  reason: "resignation" | "termination" | "contract_end";
  isolate_endpoints: boolean;
  notify_manager: boolean;
  manager_email: string;
}

interface Props {
  value: Partial<OffboardUserPayload>;
  onChange: (v: Partial<OffboardUserPayload>) => void;
}

export function OffboardUserForm({ value, onChange }: Props) {
  const set = (key: keyof OffboardUserPayload) =>
    (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      onChange({ ...value, [key]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <div className="space-y-4 text-sm">
      <div>
        <label className="block font-medium text-gray-700 mb-1">Target Email *</label>
        <input
          type="email"
          className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="alice@corp.com"
          value={value.target_email ?? ""}
          onChange={set("target_email")}
        />
      </div>

      <div>
        <label className="block font-medium text-gray-700 mb-1">Reason *</label>
        <select
          className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          value={value.reason ?? ""}
          onChange={set("reason")}
        >
          <option value="">Select reason...</option>
          <option value="resignation">Resignation</option>
          <option value="termination">Termination</option>
          <option value="contract_end">Contract End</option>
        </select>
      </div>

      <div>
        <label className="block font-medium text-gray-700 mb-1">Manager Email (for report)</label>
        <input
          type="email"
          className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="manager@corp.com"
          value={value.manager_email ?? ""}
          onChange={set("manager_email")}
        />
      </div>

      <div className="flex items-center gap-3">
        <input
          id="notify_manager"
          type="checkbox"
          className="h-4 w-4 text-indigo-600 border-gray-300 rounded"
          checked={value.notify_manager ?? true}
          onChange={set("notify_manager")}
        />
        <label htmlFor="notify_manager" className="text-gray-700">
          Notify manager with offboarding report
        </label>
      </div>

      <div className="flex items-center gap-3">
        <input
          id="isolate_endpoints"
          type="checkbox"
          className="h-4 w-4 text-red-600 border-gray-300 rounded"
          checked={value.isolate_endpoints ?? false}
          onChange={set("isolate_endpoints")}
        />
        <label htmlFor="isolate_endpoints" className="text-gray-700">
          <span className="font-medium text-red-700">Isolate endpoints</span>
          <span className="text-gray-500 ml-1">(CrowdStrike - disruptive, use for terminations)</span>
        </label>
      </div>

      <p className="text-xs text-gray-500 bg-yellow-50 border border-yellow-200 rounded p-2">
        Nexplane will resolve accounts in AD, Okta, Entra ID, Google Workspace, GitHub, and Slack
        for this email and generate one disable/removal step per connector found.
      </p>
    </div>
  );
}
