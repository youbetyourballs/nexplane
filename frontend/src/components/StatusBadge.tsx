import clsx from "clsx";
import type { ChangeRequestStatus, ExecutionStatus } from "../types/api";

const STATUS_STYLES: Record<string, string> = {
  draft: "bg-slate-100 text-slate-600",
  planned: "bg-blue-50 text-blue-700",
  safety_review: "bg-purple-50 text-purple-700",
  awaiting_approval: "bg-amber-50 text-amber-700",
  approved: "bg-emerald-50 text-emerald-700",
  executing: "bg-blue-100 text-blue-800 animate-pulse",
  verifying: "bg-indigo-50 text-indigo-700 animate-pulse",
  completed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-700",
  rolled_back: "bg-orange-100 text-orange-700",
  rejected: "bg-red-50 text-red-600",
  pending: "bg-slate-100 text-slate-600",
  running: "bg-blue-100 text-blue-800 animate-pulse",
  rolling_back: "bg-orange-100 text-orange-700 animate-pulse",
  in_progress: "bg-brand-50 text-brand-700",
  cancelled: "bg-slate-100 text-slate-500",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  planned: "Planned",
  safety_review: "Safety Review",
  awaiting_approval: "Approval Required",
  approved: "Approved",
  executing: "Executing",
  verifying: "Verifying",
  completed: "Completed",
  failed: "Failed",
  rolled_back: "Rolled Back",
  rejected: "Rejected",
  pending: "Pending",
  running: "Running",
  rolling_back: "Rolling Back",
  in_progress: "In Progress",
  cancelled: "Cancelled",
};

interface Props {
  status: ChangeRequestStatus | ExecutionStatus | string;
  size?: "sm" | "md";
}

export function StatusBadge({ status, size = "md" }: Props) {
  const style = STATUS_STYLES[status] ?? "bg-slate-100 text-slate-600";
  const label = STATUS_LABELS[status] ?? status;
  return (
    <span
      className={clsx(
        "inline-flex items-center font-medium rounded-full",
        size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs",
        style
      )}
    >
      {label}
    </span>
  );
}
