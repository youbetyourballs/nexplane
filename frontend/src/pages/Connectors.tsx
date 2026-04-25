import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plug, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import { connectorsApi } from "../api/endpoints";
import { PageHeader } from "../components/PageHeader";
import { PageLoading } from "../components/LoadingSpinner";
import type { ConnectorType, ConnectorTestResult } from "../types/api";

const CONNECTOR_LABELS: Record<ConnectorType, string> = {
  aws_mock: "AWS Mock",
  azure_mock: "Azure Mock",
  cloudflare_mock: "Cloudflare Mock",
  okta_mock: "Okta Mock",
  paloalto_mock: "Palo Alto Mock",
  ssh_runner_mock: "SSH Runner Mock",
};

const CONNECTOR_ICONS: Record<ConnectorType, string> = {
  aws_mock: "☁️",
  azure_mock: "🔷",
  cloudflare_mock: "🟠",
  okta_mock: "🔐",
  paloalto_mock: "🛡",
  ssh_runner_mock: "🖥",
};

export function Connectors() {
  const qc = useQueryClient();
  const [testResults, setTestResults] = useState<Record<string, ConnectorTestResult>>({});
  const [testing, setTesting] = useState<Record<string, boolean>>({});

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

  if (isLoading) return <PageLoading />;

  return (
    <div className="p-8">
      <PageHeader
        title="Connectors"
        subtitle="Mock connectors for infrastructure target systems"
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {(data ?? []).map((connector) => {
          const testResult = testResults[connector.id];
          const isTesting = testing[connector.id];

          return (
            <div key={connector.id} className="bg-white border border-slate-200 rounded-lg p-5">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <span className="text-2xl">{CONNECTOR_ICONS[connector.connector_type]}</span>
                  <div>
                    <div className="text-sm font-semibold text-slate-900">{connector.name}</div>
                    <div className="text-xs text-slate-400">{CONNECTOR_LABELS[connector.connector_type]}</div>
                  </div>
                </div>
                <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                  connector.status === "active"
                    ? "bg-emerald-50 text-emerald-700"
                    : connector.status === "error"
                    ? "bg-red-50 text-red-700"
                    : "bg-slate-100 text-slate-500"
                }`}>
                  {connector.status}
                </span>
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

              <button
                onClick={() => testConnector(connector.id)}
                disabled={isTesting}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 border border-slate-200 text-slate-700 text-sm rounded-md hover:bg-slate-50 disabled:opacity-50"
              >
                {isTesting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Plug className="w-3.5 h-3.5" />
                )}
                {isTesting ? "Testing..." : "Test Connector"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
