// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { X, AlertTriangle, ChevronDown } from "lucide-react";
import { backupApi, assetsApi, BackupTarget, BackupStorage } from "../api/endpoints";

const BACKUP_TYPES = [
  {
    id: "machine_image",
    label: "Machine Image",
    description: "Full OS + disk — restore to a running instance",
    available: true,
  },
  {
    id: "file_archive",
    label: "File Archive",
    description: "Directory backup via SSH/tar",
    available: true,
  },
  {
    id: "database_dump",
    label: "Database Dump",
    description: "pg_dump / mysqldump / mongodump",
    available: true,
  },
  {
    id: "storage_sync",
    label: "Storage Sync",
    description: "Cross-backend object sync",
    available: false,
  },
] as const;

type BackupTypeId = (typeof BACKUP_TYPES)[number]["id"];

function backupTypeFromStrategy(captureStrategy: string): BackupTypeId {
  if (captureStrategy === "local_files" || captureStrategy === "nfs_files") return "file_archive";
  if (captureStrategy === "database_dump" || captureStrategy === "managed_db_snapshot") return "database_dump";
  if (captureStrategy === "storage_sync") return "storage_sync";
  return "machine_image";
}

interface Props {
  target: BackupTarget | null;
  onClose: () => void;
  onSaved: () => void;
  initialAssetId?: string;
}

export function BackupTargetForm({ target, onClose, onSaved, initialAssetId }: Props) {
  const qc = useQueryClient();
  const isEdit = target !== null;

  const [assetId, setAssetId] = useState(target?.asset_id ?? initialAssetId ?? "");
  const [backupType, setBackupType] = useState<BackupTypeId>(
    target ? backupTypeFromStrategy(target.capture_strategy) : "machine_image"
  );
  const [captureStrategy, setCaptureStrategy] = useState(target?.capture_strategy ?? "ebs_snapshot");
  const [backupTier, setBackupTier] = useState(target?.backup_tier ?? "machine");
  const [storageId, setStorageId] = useState(target?.storage_id ?? "");
  const [cadenceHours, setCadenceHours] = useState(target?.expected_cadence_hours ?? 24);
  const [description, setDescription] = useState(target?.target_description ?? "");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [assetSearch, setAssetSearch] = useState("");

  const { data: assets = [] } = useQuery({
    queryKey: ["assets"],
    queryFn: () => assetsApi.list(),
  });

  const { data: storages = [] } = useQuery({
    queryKey: ["backup-storages"],
    queryFn: () => backupApi.listStorages(),
  });

  const { data: recommendation } = useQuery({
    queryKey: ["recommend-strategy", backupType, assetId],
    queryFn: () => backupApi.recommendStrategy(backupType, assetId || undefined),
    enabled: !!backupType,
  });

  // Auto-apply recommendation when backup type changes (unless user overrode)
  useEffect(() => {
    if (recommendation && !showAdvanced) {
      setCaptureStrategy(recommendation.capture_strategy);
      setBackupTier(recommendation.backup_tier);
    }
  }, [recommendation, showAdvanced]);

  const selectedAsset = assets.find((a) => a.id === assetId);
  const defaultStorage = storages.find((s: BackupStorage) => s.is_org_default);

  const filteredAssets = assets.filter((a) =>
    a.name.toLowerCase().includes(assetSearch.toLowerCase())
  );

  // Prerequisite warnings
  const warnings: string[] = [];
  if (backupType === "machine_image" && selectedAsset?.connector_type !== "aws") {
    warnings.push("Machine image backup requires an AWS connector. Configure one in Connectors.");
  }
  if (!storageId && !defaultStorage) {
    warnings.push("No backup storage configured. Add a storage destination in Backup & Recovery settings.");
  }
  if (backupType === "database_dump" && !assetId) {
    warnings.push("Select the asset hosting the database to connect.");
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      const payload = {
        target_description: description || (selectedAsset?.name ? `${selectedAsset.name} backup` : "Backup target"),
        expected_cadence_hours: cadenceHours,
        asset_id: assetId || undefined,
        backup_tier: backupTier,
        capture_strategy: captureStrategy,
        storage_id: storageId || undefined,
      };
      if (isEdit) {
        return backupApi.updateTarget(target.id, payload);
      }
      return backupApi.createTarget(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backup-targets"] });
      onSaved();
      onClose();
    },
  });

  const cadenceOptions = [
    { value: 6, label: "Every 6 hours" },
    { value: 12, label: "Every 12 hours" },
    { value: 24, label: "Daily" },
    { value: 48, label: "Every 2 days" },
    { value: 168, label: "Weekly" },
  ];

  return (
    <>
      {/* Overlay */}
      <div className="fixed inset-0 bg-black/40 z-50" onClick={onClose} />

      {/* Panel */}
      <div className="fixed right-0 inset-y-0 w-[480px] bg-white shadow-xl z-50 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-900">
            {isEdit ? "Edit Backup Target" : "Add Backup Target"}
          </h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-5">
          {/* Backup Type */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">Backup Type</label>
            <div className="grid grid-cols-2 gap-2">
              {BACKUP_TYPES.map((bt) => (
                <button
                  key={bt.id}
                  type="button"
                  disabled={!bt.available}
                  onClick={() => {
                    if (bt.available) {
                      setBackupType(bt.id);
                      setShowAdvanced(false);
                    }
                  }}
                  className={`text-left p-3 rounded-lg border text-sm transition-colors ${
                    backupType === bt.id
                      ? "border-brand-600 bg-brand-50 text-brand-900"
                      : bt.available
                      ? "border-slate-200 hover:border-slate-300 text-slate-700"
                      : "border-slate-100 bg-slate-50 text-slate-400 cursor-not-allowed"
                  }`}
                >
                  <div className="font-medium">{bt.label}</div>
                  <div className="text-xs mt-0.5 opacity-75">{bt.description}</div>
                  {!bt.available && (
                    <div className="text-xs mt-1 text-slate-400 italic">Coming soon</div>
                  )}
                </button>
              ))}
            </div>
            {recommendation && !showAdvanced && (
              <p className="text-xs text-slate-500 mt-2">{recommendation.reason}</p>
            )}
          </div>

          {/* Asset */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Asset</label>
            <input
              type="text"
              placeholder="Search assets…"
              value={assetSearch}
              onChange={(e) => setAssetSearch(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 mb-1"
            />
            <select
              value={assetId}
              onChange={(e) => setAssetId(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              size={Math.min(filteredAssets.length + 1, 5)}
            >
              <option value="">— no specific asset —</option>
              {filteredAssets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ({a.asset_type})
                </option>
              ))}
            </select>
          </div>

          {/* Storage */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Storage Destination
            </label>
            <select
              value={storageId}
              onChange={(e) => setStorageId(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="">
                {defaultStorage ? `Default: ${defaultStorage.name}` : "— select storage —"}
              </option>
              {storages.map((s: BackupStorage) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.storage_type})
                </option>
              ))}
            </select>
          </div>

          {/* Cadence */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">Backup Cadence</label>
            <select
              value={cadenceHours}
              onChange={(e) => setCadenceHours(Number(e.target.value))}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              {cadenceOptions.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1">
              Description <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={selectedAsset ? `${selectedAsset.name} backup` : "Backup target description"}
              className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>

          {/* Advanced */}
          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700"
            >
              <ChevronDown className={`w-3.5 h-3.5 transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
              Advanced — override strategy
            </button>
            {showAdvanced && recommendation && (
              <div className="mt-3 space-y-2">
                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1">Capture Strategy</label>
                  <select
                    value={captureStrategy}
                    onChange={(e) => setCaptureStrategy(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                  >
                    <option value={recommendation.capture_strategy}>
                      {recommendation.capture_strategy} (recommended)
                    </option>
                    {recommendation.alternatives.map((alt) => (
                      <option key={alt.capture_strategy} value={alt.capture_strategy}>
                        {alt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>

          {/* Warnings */}
          {warnings.length > 0 && (
            <div className="rounded-md bg-amber-50 border border-amber-200 p-3 space-y-1">
              {warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-amber-800">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}

          {/* Save error */}
          {saveMutation.isError && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
              Failed to save backup target. Please try again.
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-slate-200 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm text-slate-600 border border-slate-300 rounded-md hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="px-4 py-2 text-sm font-medium text-white bg-brand-600 rounded-md hover:bg-brand-700 disabled:opacity-50"
          >
            {saveMutation.isPending ? "Saving…" : isEdit ? "Save Changes" : "Add Backup Target"}
          </button>
        </div>
      </div>
    </>
  );
}
