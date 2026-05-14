import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface Notification {
  id: string;
  event_type: string;
  resource_type: string | null;
  resource_id: string | null;
  message: string;
  read: boolean;
  created_at: string;
}

export function Notifications() {
  const qc = useQueryClient();
  const { data: notifications = [], isLoading } = useQuery<Notification[]>({
    queryKey: ["notifications"],
    queryFn: () => apiClient.get("/notifications").then(r => r.data),
    refetchInterval: 15_000,
  });

  const markAllRead = useMutation({
    mutationFn: () => apiClient.post("/notifications/read-all"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });

  if (isLoading) return <div className="p-6">Loading...</div>;

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold">Notifications</h1>
        <button
          onClick={() => markAllRead.mutate()}
          className="text-sm text-blue-600 hover:underline"
        >
          Mark all read
        </button>
      </div>
      <div className="space-y-2">
        {notifications.length === 0 && (
          <p className="text-slate-500 text-sm italic">No notifications.</p>
        )}
        {notifications.map(n => (
          <div
            key={n.id}
            className={`p-3 rounded border text-sm ${
              n.read
                ? "bg-white border-slate-200 text-slate-500"
                : "bg-blue-50 border-blue-200 text-slate-800 font-medium"
            }`}
          >
            <p>{n.message}</p>
            <p className="text-xs text-slate-400 mt-1">
              {new Date(n.created_at).toLocaleString()}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
