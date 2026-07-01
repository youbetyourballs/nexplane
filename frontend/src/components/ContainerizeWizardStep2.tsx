// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { useFireCR } from "../hooks/useFireCR";

interface App {
  name: string;
  binary: string;
  listening_ports: { port: number; protocol: string }[];
  data_directories: string[];
  env_vars: string[];
  containerization_status: string;
  image_digest?: string;
}

interface BuildResult {
  imageDigest: string;
  manifests: Record<string, string>;
  dockerfile?: string;
}

interface ContainerizeWizardStep2Props {
  assetId: string;
  apps: App[];
  onComplete: (buildResults: Record<string, BuildResult>) => void;
}

export function ContainerizeWizardStep2({ assetId, apps, onComplete }: ContainerizeWizardStep2Props) {
  const [registry, setRegistry] = useState("");
  const [namespace, setNamespace] = useState("default");
  const [expandedApp, setExpandedApp] = useState<string | null>(null);
  const [buildResults, setBuildResults] = useState<Record<string, BuildResult>>({});
  const [status, setStatus] = useState<"idle" | "building" | "done" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const { fireCR, loading } = useFireCR();

  const handleBuildAndPush = async () => {
    if (!registry) { setError("Registry URL is required"); return; }
    setStatus("building"); setError(null);
    const results: Record<string, BuildResult> = {};
    for (const app of apps) {
      try {
        const crResult = await fireCR({
          title: `[Containerize] Build & push ${app.name}`,
          changeType: "agent_containerize_build",
          targetAssetId: assetId,
          parameters: { app_name: app.name, registry, namespace, dry_run: false },
        });
        results[app.name] = {
          imageDigest: (crResult?.image_digest as string) ?? "",
          manifests: (crResult?.manifests as Record<string, string>) ?? {},
          dockerfile: (crResult?.dockerfile as string) ?? "",
        };
      } catch (e) {
        setError(`Failed to build ${app.name}: ${e instanceof Error ? e.message : "Unknown error"}`);
        setStatus("error"); return;
      }
    }
    setBuildResults(results); setStatus("done"); onComplete(results);
  };

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-semibold text-slate-900">Step 2 — Build &amp; Push</h3>
        <p className="text-sm text-slate-500 mt-1">Generate Dockerfiles and push images to your container registry.</p>
      </div>

      <div className="border border-slate-200 rounded-lg p-4 space-y-4">
        <p className="text-sm font-semibold text-slate-700">Registry Configuration</p>
        <div className="space-y-1">
          <label htmlFor="registry" className="block text-sm font-medium text-slate-700">Registry URL</label>
          <input
            id="registry"
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
            placeholder="123456789.dkr.ecr.us-east-1.amazonaws.com/nexplane"
            value={registry}
            onChange={(e) => setRegistry(e.target.value)}
            disabled={status === "building"}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="namespace" className="block text-sm font-medium text-slate-700">Kubernetes Namespace</label>
          <input
            id="namespace"
            className="w-full text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500"
            placeholder="default"
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            disabled={status === "building"}
          />
        </div>
      </div>

      <div className="space-y-3">
        {apps.map((app) => {
          const result = buildResults[app.name];
          const isOpen = expandedApp === app.name;
          return (
            <div key={app.name} className="border border-slate-200 rounded-lg overflow-hidden">
              <div
                className="flex items-center justify-between p-3 hover:bg-slate-50 cursor-pointer"
                onClick={() => setExpandedApp(isOpen ? null : app.name)}
              >
                <div className="flex items-center gap-3">
                  {isOpen ? <ChevronDown className="h-4 w-4 text-slate-400" /> : <ChevronRight className="h-4 w-4 text-slate-400" />}
                  <span className="font-mono text-sm font-medium text-slate-800">{app.name}</span>
                  {app.data_directories.length > 0 && (
                    <span className="text-xs border border-slate-300 rounded px-1.5 py-0.5 text-slate-500">stateful</span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {result?.imageDigest && (
                    <span className="text-xs text-slate-400 font-mono">{result.imageDigest.slice(0, 20)}&hellip;</span>
                  )}
                  {result ? (
                    <CheckCircle className="h-4 w-4 text-green-500" />
                  ) : status === "building" ? (
                    <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
                  ) : null}
                </div>
              </div>
              {isOpen && (
                <div className="border-t border-slate-200 p-3 space-y-3">
                  {result?.dockerfile ? (
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">Generated Dockerfile</p>
                      <pre className="bg-slate-50 rounded p-3 text-xs font-mono overflow-x-auto whitespace-pre border border-slate-200">{result.dockerfile}</pre>
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400">Dockerfile preview available after build.</p>
                  )}
                  <div className="grid grid-cols-2 gap-2 text-xs text-slate-500">
                    <div>
                      <span className="font-medium">Ports: </span>
                      {app.listening_ports.map((p) => `${p.port}/${p.protocol}`).join(", ") || "none"}
                    </div>
                    <div><span className="font-medium">Env vars: </span>{app.env_vars.length}</div>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-red-600">
          <AlertCircle className="h-4 w-4" />{error}
        </div>
      )}

      {status === "done" ? (
        <div className="flex items-center gap-2 text-sm text-green-600">
          <CheckCircle className="h-4 w-4" />All images built and pushed successfully.
        </div>
      ) : (
        <button
          onClick={handleBuildAndPush}
          disabled={!registry || status === "building" || loading}
          className="w-full px-4 py-2 bg-brand-600 text-white text-sm font-medium rounded-md hover:bg-brand-700 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
        >
          {status === "building" ? (
            <><Loader2 className="h-4 w-4 animate-spin" />Building &amp; pushing&hellip;</>
          ) : "Build & Push"}
        </button>
      )}
    </div>
  );
}
