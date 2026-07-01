// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

interface ReleaseManifest {
  version: string;
  released_at: string;
  min_compatible_version: string;
  image_sha256: string;
  changelog_url?: string;
  severity: "recommended" | "security" | "critical";
}

interface VersionCheckResponse {
  available: boolean;
  manifest: ReleaseManifest | null;
  degraded: Record<string, unknown> | null;
}

const SEVERITY_STYLES: Record<ReleaseManifest["severity"], string> = {
  recommended: "bg-blue-600 text-white",
  security: "bg-amber-500 text-white",
  critical: "bg-red-600 text-white",
};

const SEVERITY_LABELS: Record<ReleaseManifest["severity"], string> = {
  recommended: "Update available",
  security: "Security update available",
  critical: "Critical update required",
};

export default function UpdateBanner({ isAdmin }: { isAdmin: boolean }) {
  const [manifest, setManifest] = useState<ReleaseManifest | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    if (!isAdmin) return;
    fetch("/version/check")
      .then((r) => r.json())
      .then((data: VersionCheckResponse) => {
        if (data.available && data.manifest) {
          setManifest(data.manifest);
        }
      })
      .catch(() => {/* silently ignore */});
  }, [isAdmin]);

  if (!isAdmin || !manifest || (dismissed && manifest.severity !== "critical")) {
    return null;
  }

  const severity = manifest.severity ?? "recommended";

  function handleReview() {
    const params = new URLSearchParams({
      changeType: "platform_upgrade",
      prefill: JSON.stringify({
        target_version: manifest!.version,
        image_sha256: manifest!.image_sha256,
        changelog_url: manifest!.changelog_url ?? "",
        require_approval: "true",
      }),
    });
    navigate(`/change-requests/new?${params}`);
  }

  return (
    <div className={`w-full px-4 py-2 flex items-center justify-between text-sm ${SEVERITY_STYLES[severity]}`}>
      <span>
        <strong>{SEVERITY_LABELS[severity]}:</strong> Nexplane {manifest.version} is available.{" "}
        {manifest.changelog_url && (
          <a href={manifest.changelog_url} target="_blank" rel="noopener noreferrer" className="underline ml-1">
            What&apos;s new
          </a>
        )}
      </span>
      <div className="flex gap-2 ml-4">
        <button
          onClick={handleReview}
          className="bg-white text-gray-900 px-3 py-1 rounded text-xs font-semibold hover:bg-gray-100"
        >
          Review update
        </button>
        {severity !== "critical" && (
          <button
            onClick={() => setDismissed(true)}
            className="opacity-75 hover:opacity-100 px-2 text-xs"
            aria-label="Dismiss"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
