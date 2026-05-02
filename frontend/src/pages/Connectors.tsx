import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plug, CheckCircle2, XCircle, Loader2, Download, Trash2 } from "lucide-react";
import { connectorsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import CredentialModal from "../components/CredentialModal";
import ScheduleModal from "../components/ScheduleModal";
import AddConnectorModal from "../components/AddConnectorModal";
import type { ConnectorRead, ConnectorType, ConnectorTestResult, IngestResponse } from "../types/api";

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
};

// Ingest action IDs per connector type (connectors that support discovery)
const INGEST_ACTIONS: Partial<Record<ConnectorType, string>> = {
  active_directory: "discover_computers",
  crowdstrike: "discover_endpoints",
  tenable: "discover_assets",
  azure: "discover_vms",
  paloalto: "ingest_traffic_logs",
};

const INTERVAL_LABELS: Record<number, string> = {
  1: "every hour",
  6: "every 6 hours",
  24: "every 24 hours",
  168: "weekly",
};

function ConnectorScheduleBadge({
  connector,
  token,
  onConfigure,
}: {
  connector: ConnectorRead;
  token: string;
  onConfigure: (s: any) => void;
}) {
  const { data: schedule } = useQuery({
    queryKey: ["schedule", connector.id],
    queryFn: () =>
      fetch(`/connectors/${connector.id}/schedule`, {
        headers: { Authorization: `Bearer ${token}` },
      })
        .then((r) => (r.status === 404 || r.status === 204 ? null : r.json()))
        .catch(() => null),
    staleTime: 30000,
  });

  const intervalLabel = schedule
    ? (INTERVAL_LABELS[schedule.interval_hours] ?? `every ${schedule.interval_hours}h`)
    : null;

  return (
    <div className="flex items-center gap-2 mt-1">
      {schedule ? (
        <>
          <span className="text-xs text-green-600">&#x23F0; {intervalLabel}</span>
          {schedule.last_run_status === "error" && (
            <span className="text-xs text-red-500">&#x2717; Last failed</span>
          )}
          {schedule.last_run_status === "success" && (
            <span className="text-xs text-gray-400">&#x2713; Synced</span>
          )}
          <button onClick={() => onConfigure(schedule)} className="text-xs text-indigo-600 hover:underline">
            Edit
          </button>
        </>
      ) : (
        <button onClick={() => onConfigure(null)} className="text-xs text-gray-400 hover:text-indigo-600">
          + Schedule sync
        </button>
      )}
    </div>
  );
}

function ConnectorCredentialBadge({
  connector,
  token,
  onConfigure,
}: {
  connector: ConnectorRead;
  token: string;
  onConfigure: () => void;
}) {
  const { data } = useQuery({
    queryKey: ["credentials", connector.id],
    queryFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, {
        headers: { Authorization: `Bearer ${token}` },
      }).then((r) => r.json()),
    staleTime: 30000,
  });

  if (!data || data.fields?.length === 0) return null;

  return (
    <div className="flex items-center gap-2 mt-1">
      <span className={`text-xs ${data.configured ? "text-green-600" : "text-amber-500"}`}>
        {data.configured ? "🔒 Credentials configured" : "🔓 No credentials"}
      </span>
      <button onClick={onConfigure} className="text-xs text-indigo-600 hover:underline">
        {data.configured ? "Update" : "Configure"}
      </button>
    </div>
  );
}

export function Connectors() {
  const qc = useQueryClient();
  const [testResults, setTestResults] = useState<Record<string, ConnectorTestResult>>({});
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [ingestResults, setIngestResults] = useState<Record<string, IngestResponse>>({});
  const [credModalConnector, setCredModalConnector] = useState<ConnectorRead | null>(null);
  const [scheduleModal, setScheduleModal] = useState<{ connector: ConnectorRead; existing: any } | null>(null);
  const [addModalOpen, setAddModalOpen] = useState(false);

  const token = localStorage.getItem("nexplane_token") ?? "";

  const { data, isLoading } = useQuery({
    queryKey: ["connectors"],
    queryFn: connectorsApi.list,
  });

  async function testConnector(id: string) {
    setTesting((prev) => ({ ...prev, [id]: true }));
    try {
      const result = await connectorsApi.test(id);
      setTestResults((prev) => ({ ...prev, [id]: result }));
    } finally {
      setTesting((prev) => ({ ...prev, [id]: false }));
    }
  }

  const [deletingId, setDeletingId] = useState<string | null>(null);

  const deleteMutation = useMutation({
    mutationFn: (id: string) => connectorsApi.delete(id),
    onSuccess: (_: unknown, id: string) => {
      setDeletingId(null);
      qc.invalidateQueries({ queryKey: ["connectors"] });
    },
  });

  const ingestMutation = useMutation({
    mutationFn: ({ id, actionId }: { id: string; actionId: string }) =>
      connectorsApi.ingest(id, actionId),
    onSuccess: (data, variables) => {
      setIngestResults((prev) => ({ ...prev, [variables.id]: data }));
      qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <PageHeader
          title="Connectors"
          subtitle="Integrations with infrastructure target systems"
        />
        <button
          onClick={() => setAddModalOpen(true)}
          className="inline-flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-md hover:bg-indigo-700"
        >
          <span className="text-base leading-none">+</span>
          Add Connector
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(data ?? []).map((connector) => {
          const testResult = testResults[connector.id];
          const isTesting = testing[connector.id];
          const ingestResult = ingestResults[connector.id];
          const ingestActionId = INGEST_ACTIONS[connector.connector_type];
          const isIngesting = ingestMutation.isPending && ingestMutation.variables?.id === connector.id;

          return (
            <div key={connector.id} className="bg-white border border-slate-200 rounded-lg p-5">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{CONNECTOR_ICONS[connector.connector_type]}</span>
                  <div>
                    <div className="text-sm font-semibold text-slate-900">{connector.name}</div>
                    <div className="text-xs text-slate-400">{CONNECTOR_LABELS[connector.connector_type]}</div>
                    <ConnectorCredentialBadge
                      connector={connector}
                      token={token}
                      onConfigure={() => setCredModalConnector(connector)}
                    />
                    {ingestActionId && (
                      <ConnectorScheduleBadge
                        connector={connector}
                        token={token}
                        onConfigure={(existing) => setScheduleModal({ connector, existing })}
                      />
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                    connector.status === "active"
                      ? "bg-emerald-50 text-emerald-700"
                      : connector.status === "error"
                      ? "bg-red-50 text-red-700"
                      : "bg-slate-100 text-slate-500"
                  }`}>
                    {connector.status}
                  </span>

                  {deletingId === connector.id ? (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => deleteMutation.mutate(connector.id)}
                        disabled={deleteMutation.isPending}
                        className="text-xs text-red-600 font-medium hover:text-red-800 disabled:opacity-50"
                      >
                        {deleteMutation.isPending && deleteMutation.variables === connector.id
                          ? "Deleting…"
                          : "Confirm delete"}
                      </button>
                      <button
                        onClick={() => setDeletingId(null)}
                        className="text-xs text-slate-400 hover:text-slate-600"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setDeletingId(connector.id)}
                      className="text-slate-300 hover:text-red-400 transition-colors"
                      title="Delete connector"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>

              <div className="mb-3">
                <div className="text-xs text-slate-400 mb-1.5">Scoped Permissions</div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(connector.scoped_permissions).map(([scope, actions]) => (
                    <span key={scope} className="px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-xs font-mono">
                      {scope}: {(actions as string[]).join(", ")}
                    </span>
                  ))}
                </div>
              </div>

              {testResult && (
                <div className={`mb-3 p-2.5 rounded border text-xs ${
                  testResult.success
                    ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                    : "bg-red-50 border-red-200 text-red-700"
                }`}>
                  <div className="flex items-center gap-1.5 mb-0.5">
                    {testResult.success ? <CheckCircle2 className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                    <span className="font-medium">{testResult.message}</span>
                  </div>
                  <div className="text-slate-500">Latency: {testResult.latency_ms}ms</div>
                </div>
              )}

              {ingestResult && (
                <div className="mb-3 p-2.5 rounded border text-xs bg-brand-50 border-brand-200 text-brand-700">
                  <span className="font-medium">Discovery complete — </span>
                  {ingestResult.created} created, {ingestResult.updated} updated
                </div>
              )}

              <div className="flex gap-2">
                <button
                  onClick={() => testConnector(connector.id)}
                  disabled={isTesting}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-200 text-slate-700 text-sm rounded-md hover:bg-slate-50 disabled:opacity-50"
                >
                  {isTesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
                  {isTesting ? "Testing..." : "Test Connector"}
                </button>

                {ingestActionId && (
                  <button
                    onClick={() => ingestMutation.mutate({ id: connector.id, actionId: ingestActionId })}
                    disabled={isIngesting}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-brand-200 text-brand-700 text-sm rounded-md hover:bg-brand-50 disabled:opacity-50"
                  >
                    {isIngesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                    {isIngesting ? "Discovering..." : "Run Discovery"}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {credModalConnector && (
        <CredentialModal
          connector={credModalConnector}
          token={token}
          onClose={() => setCredModalConnector(null)}
        />
      )}
      {scheduleModal && (
        <ScheduleModal
          connector={scheduleModal.connector}
          existing={scheduleModal.existing}
          token={token}
          onClose={() => setScheduleModal(null)}
        />
      )}
      {addModalOpen && (
        <AddConnectorModal
          token={token}
          onClose={() => setAddModalOpen(false)}
          onCreated={(connector) => {
            setAddModalOpen(false);
            qc.invalidateQueries({ queryKey: ["connectors"] });
            setCredModalConnector(connector);
          }}
        />
      )}
    </div>
  );
}
