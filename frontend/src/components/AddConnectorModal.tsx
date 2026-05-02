import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { connectorsApi } from "../api/endpoints";
import type { ConnectorRead, ConnectorType } from "../types/api";

const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  aws_mock: "AWS",
  azure_mock: "Azure",
  cloudflare_mock: "Cloudflare",
  okta_mock: "Okta",
  paloalto_mock: "Palo Alto",
  ssh_runner_mock: "SSH Runner",
  active_directory_mock: "Active Directory",
  crowdstrike_mock: "CrowdStrike Falcon",
  tenable_mock: "Tenable",
  nexplane_agent: "Nexplane Agent",
};

const CONNECTOR_ICONS: Record<ConnectorType, string> = {
  aws_mock: "☁️",
  azure_mock: "🔷",
  cloudflare_mock: "🟠",
  okta_mock: "🔐",
  paloalto_mock: "🛡️",
  ssh_runner_mock: "🖥️",
  active_directory_mock: "🏢",
  crowdstrike_mock: "🦅",
  tenable_mock: "🔍",
  nexplane_agent: "🤖",
};

const ALL_TYPES = Object.keys(CONNECTOR_LABELS) as ConnectorType[];

interface Props {
  token: string;
  onClose: () => void;
  onCreated: (connector: ConnectorRead) => void;
}

export default function AddConnectorModal({ token, onClose, onCreated }: Props) {
  const [connectorType, setConnectorType] = useState<ConnectorType>("aws_mock");
  const [name, setName] = useState<string>(CONNECTOR_LABELS["aws_mock"]);

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
