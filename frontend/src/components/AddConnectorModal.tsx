import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { connectorsApi } from "../api/endpoints";
import type { ConnectorRead, ConnectorType } from "../types/api";

const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  aws: "Amazon Web Services",
  azure: "Microsoft Azure",
  cloudflare: "Cloudflare",
  okta: "Okta",
  paloalto: "Palo Alto Networks",
  ssh: "SSH Runner",
  active_directory: "Active Directory",
  crowdstrike: "CrowdStrike Falcon",
  tenable: "Tenable",
  nexplane_agent: "Nexplane Agent",
  gcp: "Google Cloud Platform",
  runzero: "RunZero",
  wiz: "Wiz",
  entra_id: "Microsoft Entra ID",
  azure_ad: "Azure AD (Graph API)",
  sentinelone: "SentinelOne",
  defender_endpoint: "Microsoft Defender for Endpoint",
  hashicorp_vault: "HashiCorp Vault",
  github: "GitHub",
  kubernetes: "Kubernetes",
  snyk: "Snyk",
  qualys: "Qualys",
  terraform: "Terraform (HCP)",
  ansible: "Ansible (AWX)",
  cloudformation: "AWS CloudFormation",
  pulumi: "Pulumi",
  helm: "Helm",
  bicep: "Azure Bicep",
  checkov: "Checkov",
  saltstack: "SaltStack",
  chef_inspec: "Chef InSpec",
  jira: "Jira",
  pagerduty: "PagerDuty",
  servicenow: "ServiceNow",
  splunk: "Splunk",
  datadog: "Datadog",
  zscaler: "Zscaler",
  google_workspace: "Google Workspace",
  tailscale: "Tailscale",
  terraform_local: "Terraform (Local CLI)",
  ansible_local: "Ansible (Local CLI)",
  oci: "Oracle Cloud Infrastructure",
};

const CONNECTOR_ICONS: Record<ConnectorType, string> = {
  aws: "☁️",
  azure: "🔷",
  cloudflare: "🟠",
  okta: "🔐",
  paloalto: "🛡️",
  ssh: "🖥️",
  active_directory: "🏢",
  crowdstrike: "🦅",
  tenable: "🔍",
  nexplane_agent: "🤖",
  gcp: "☁️",
  runzero: "🌐",
  wiz: "🛡️",
  entra_id: "👥",
  azure_ad: "👥",
  sentinelone: "🛡️",
  defender_endpoint: "🛡️",
  hashicorp_vault: "🔒",
  github: "🐙",
  kubernetes: "⎈",
  snyk: "🐛",
  qualys: "🔍",
  terraform: "🏗️",
  ansible: "⚙️",
  cloudformation: "☁️",
  pulumi: "🏗️",
  helm: "⎈",
  bicep: "🔷",
  checkov: "✅",
  saltstack: "🧂",
  chef_inspec: "👨‍🍳",
  jira: "🎫",
  pagerduty: "🔔",
  servicenow: "❄️",
  splunk: "📊",
  datadog: "🐕",
  zscaler: "🛡️",
  google_workspace: "🌐",
  tailscale: "🔒",
  terraform_local: "🏗️",
  ansible_local: "⚙️",
  oci: "🔶",
};

const ALL_TYPES = (Object.keys(CONNECTOR_LABELS) as ConnectorType[]).filter(
  (t) => t !== "nexplane_agent"
);

interface Props {
  token: string;
  onClose: () => void;
  onCreated: (connector: ConnectorRead) => void;
}

export default function AddConnectorModal({ token, onClose, onCreated }: Props) {
  const [connectorType, setConnectorType] = useState<ConnectorType>("aws");
  const [name, setName] = useState<string>(CONNECTOR_LABELS["aws"]);

  function handleTypeChange(type: ConnectorType) {
    setConnectorType(type);
    setName(CONNECTOR_LABELS[type]);
  }

  const createMutation = useMutation({
    mutationFn: () =>
      connectorsApi.create({
        connector_type: connectorType,
        name: name.trim(),
        scoped_permissions: {},
      }),
    onSuccess: (newConnector) => {
      onCreated(newConnector);
      onClose();
    },
  });

  const canSubmit = name.trim().length > 0 && !createMutation.isPending;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold text-slate-900 mb-1">Add Connector</h2>
        <p className="text-sm text-slate-500 mb-5">
          Choose a connector type and give it a name. You'll configure credentials next.
        </p>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Connector Type
            </label>
            <select
              value={connectorType}
              onChange={(e) => handleTypeChange(e.target.value as ConnectorType)}
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            >
              {ALL_TYPES.map((type) => (
                <option key={type} value={type}>
                  {CONNECTOR_ICONS[type]} {CONNECTOR_LABELS[type]}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Display Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Production AWS"
              className="w-full border border-slate-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        {createMutation.isError && (
          <p className="text-sm text-red-500 mt-3">
            Failed to create connector. Please try again.
          </p>
        )}

        <div className="flex gap-3 mt-6">
          <button
            onClick={() => createMutation.mutate()}
            disabled={!canSubmit}
            className="flex-1 bg-indigo-600 text-white rounded-md py-2 text-sm font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {createMutation.isPending ? "Adding…" : "Add Connector"}
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
