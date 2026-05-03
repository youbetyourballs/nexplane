import { X } from "lucide-react";

interface FreezeAlertProps {
  reason: string;
  endAt: string;
  onDismiss: () => void;
}

export function FreezeAlert({ reason, endAt, onDismiss }: FreezeAlertProps) {
  const endDate = new Date(endAt).toLocaleString();
  return (
    <div className="bg-amber-500 text-amber-950 px-4 py-2 flex items-center justify-between gap-4 text-sm font-medium">
      <span>
        <strong>Change Freeze Active</strong> — Changes cannot be approved or executed until{" "}
        <strong>{endDate}</strong>. Reason: {reason}
      </span>
      <button
        onClick={onDismiss}
        className="flex-shrink-0 text-amber-900 hover:text-amber-950"
        aria-label="Dismiss freeze banner"
      >
        <X size={16} />
      </button>
    </div>
  );
}
