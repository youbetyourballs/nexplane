import type { ChangeEvent } from "react";

export interface OnboardUserPayload {
  target_email: string;
  display_name: string;
  department: string;
  manager_email: string;
  ad_ou: string;
  ad_groups: string;          // comma-separated in UI, split before submit
  okta_groups: string;
  google_org_unit: string;
  github_teams: string;
  slack_channels: string;
}

interface Props {
  value: Partial<OnboardUserPayload>;
  onChange: (v: Partial<OnboardUserPayload>) => void;
}

function Field({
  label, id, value, onChange, type = "text", placeholder, hint,
}: {
  label: string; id: keyof OnboardUserPayload; value: string; placeholder?: string; hint?: string;
  onChange: (key: keyof OnboardUserPayload, val: string) => void; type?: string;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type}
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(id, e.target.value)}
      />
      {hint && <p className="text-xs text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

export function OnboardUserForm({ value, onChange }: Props) {
  const set = (key: keyof OnboardUserPayload, val: string) => onChange({ ...value, [key]: val });

  return (
    <div className="space-y-4">
      <Field label="New User Email *" id="target_email" value={value.target_email ?? ""} onChange={set} type="email" placeholder="new.employee@corp.com" />
      <Field label="Display Name *" id="display_name" value={value.display_name ?? ""} onChange={set} placeholder="Jane Smith" />
      <Field label="Department *" id="department" value={value.department ?? ""} onChange={set} placeholder="Engineering" />
      <Field label="Manager Email *" id="manager_email" value={value.manager_email ?? ""} onChange={set} type="email" placeholder="manager@corp.com" />

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Active Directory options</summary>
        <div className="mt-3 space-y-3">
          <Field label="OU Path" id="ad_ou" value={value.ad_ou ?? ""} onChange={set} placeholder="OU=Engineering,DC=corp,DC=example,DC=com" hint="Leave blank for default OU" />
          <Field label="AD Groups (comma-separated DNs)" id="ad_groups" value={value.ad_groups ?? ""} onChange={set} placeholder="CN=VPN-Users,OU=Groups,DC=corp,DC=example,DC=com" />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Okta options</summary>
        <div className="mt-3">
          <Field label="Okta Group IDs (comma-separated)" id="okta_groups" value={value.okta_groups ?? ""} onChange={set} placeholder="00g1ab2cd3ef4gh5ij,00g..." />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Google Workspace options</summary>
        <div className="mt-3">
          <Field label="Org Unit Path" id="google_org_unit" value={value.google_org_unit ?? ""} onChange={set} placeholder="/Engineering" />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">GitHub options</summary>
        <div className="mt-3">
          <Field label="Team slugs (comma-separated)" id="github_teams" value={value.github_teams ?? ""} onChange={set} placeholder="backend,infra" />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Slack options</summary>
        <div className="mt-3">
          <Field label="Channel IDs to pre-join (comma-separated)" id="slack_channels" value={value.slack_channels ?? ""} onChange={set} placeholder="C01234ABCDE,C09876ZYXWV" />
        </div>
      </details>
    </div>
  );
}
