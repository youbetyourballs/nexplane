import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  accessReviewsApi,
  type AccessReviewOut,
  type AccessReviewDecisionItem,
} from "../api/accessReviews";

// ─── Status badge ────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    collecting: "bg-yellow-100 text-yellow-800",
    awaiting_approval: "bg-blue-100 text-blue-800",
    approved: "bg-indigo-100 text-indigo-800",
    generating_changes: "bg-purple-100 text-purple-800",
    completed: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${colors[status] ?? "bg-gray-100 text-gray-800"}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ─── New Review Modal ─────────────────────────────────────────────────────────

function NewReviewModal({ onClose }: { onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const create = useMutation({
    mutationFn: () =>
      accessReviewsApi.create({
        title,
        scope: { connector_ids: null, groups: null, user_emails: null },
      }),
    onSuccess: (review) => {
      qc.invalidateQueries({ queryKey: ["access-reviews"] });
      onClose();
      navigate(`/access-reviews/${review.id}`);
    },
    onError: () => setError("Failed to create review"),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold">New Access Review</h2>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
          <input
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="e.g. Q2 2026 Access Review — Engineering"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <p className="text-xs text-gray-500">
          Scope: all connectors, all users. Connector and group filters coming soon.
        </p>
        <div className="flex justify-end gap-2">
          <button className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded" onClick={onClose}>
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
            disabled={!title.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Creating..." : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── List View ────────────────────────────────────────────────────────────────

function AccessReviewList() {
  const [showModal, setShowModal] = useState(false);
  const { data: reviews, isLoading, error } = useQuery({
    queryKey: ["access-reviews"],
    queryFn: accessReviewsApi.list,
  });

  if (isLoading) return <p className="p-6 text-gray-500">Loading...</p>;
  if (error) return <p className="p-6 text-red-600">Failed to load access reviews.</p>;

  return (
    <div className="p-6 space-y-4">
      {showModal && <NewReviewModal onClose={() => setShowModal(false)} />}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Access Reviews</h1>
        <button
          className="px-4 py-2 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700"
          onClick={() => setShowModal(true)}
        >
          New Review
        </button>
      </div>

      {reviews?.length === 0 ? (
        <p className="text-sm text-gray-500">No access reviews yet. Create one to get started.</p>
      ) : (
        <div className="overflow-hidden border border-gray-200 rounded-lg">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Title</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Status</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {reviews?.map((r) => (
                <tr key={r.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link
                      to={`/access-reviews/${r.id}`}
                      className="text-indigo-600 hover:underline font-medium"
                    >
                      {r.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {new Date(r.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Detail View ──────────────────────────────────────────────────────────────

function AccessReviewDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const { data: review, isLoading, error } = useQuery({
    queryKey: ["access-review", id],
    queryFn: () => accessReviewsApi.get(id!),
    enabled: !!id,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "collecting" || status === "generating_changes" ? 3000 : false;
    },
  });

  const approve = useMutation({
    mutationFn: () => accessReviewsApi.approve(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["access-review", id] });
      qc.invalidateQueries({ queryKey: ["access-reviews"] });
    },
  });

  const submitDecision = useMutation({
    mutationFn: (payload: { entry_id: string; decision: "keep" | "revoke" }) =>
      accessReviewsApi.submitDecisions(id!, {
        decisions: {
          [payload.entry_id]: { decision: payload.decision, note: "" },
        },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access-review", id] }),
  });

  if (isLoading) return <p className="p-6 text-gray-500">Loading...</p>;
  if (error || !review) return <p className="p-6 text-red-600">Review not found.</p>;

  const entries: any[] = review.snapshot?.entries ?? [];
  const decisions = review.decisions ?? {};

  const allDecided = entries.length === 0 || entries.every((e) => decisions[e.entry_id]);
  const revokeCount = Object.values(decisions).filter((d) => d.decision === "revoke").length;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/access-reviews" className="text-sm text-gray-500 hover:text-gray-700">
            Back to Access Reviews
          </Link>
          <h1 className="text-xl font-semibold text-gray-900 mt-1">{review.title}</h1>
        </div>
        <StatusBadge status={review.status} />
      </div>

      {/* Summary */}
      <div className="grid grid-cols-3 gap-4 text-sm text-gray-600">
        <div>
          <span className="font-medium">Collected:</span>{" "}
          {review.collected_at ? new Date(review.collected_at).toLocaleString() : "-"}
        </div>
        <div>
          <span className="font-medium">Entries:</span> {entries.length}
        </div>
        <div>
          <span className="font-medium">To revoke:</span> {revokeCount}
        </div>
      </div>

      {/* Approve button */}
      {review.status === "awaiting_approval" && (
        <div className="flex items-center gap-4">
          <button
            className="px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:opacity-50"
            disabled={!allDecided || approve.isPending}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? "Approving..." : `Approve & Generate ${revokeCount} Change Request${revokeCount !== 1 ? "s" : ""}`}
          </button>
          {!allDecided && (
            <p className="text-xs text-gray-500">
              {entries.filter((e) => !decisions[e.entry_id]).length} entries still need a decision.
            </p>
          )}
        </div>
      )}

      {/* Entries table */}
      {entries.length > 0 ? (
        <div className="overflow-hidden border border-gray-200 rounded-lg">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-500">User</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Access</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Connector</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Risk</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Decision</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {entries.map((e) => {
                const d: AccessReviewDecisionItem | undefined = decisions[e.entry_id];
                return (
                  <tr key={e.entry_id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{e.user_display_name}</div>
                      <div className="text-gray-500 text-xs">{e.user_email}</div>
                    </td>
                    <td className="px-4 py-3 text-gray-700">{e.access_label}</td>
                    <td className="px-4 py-3 text-gray-500">{e.connector_type?.replace(/_/g, " ")}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                          e.risk_level === "high"
                            ? "bg-red-100 text-red-700"
                            : e.risk_level === "medium"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-green-100 text-green-700"
                        }`}
                      >
                        {e.risk_level}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {review.status === "awaiting_approval" ? (
                        <div className="flex gap-2">
                          <button
                            className={`px-3 py-1 rounded text-xs font-medium border ${
                              d?.decision === "keep"
                                ? "bg-green-600 text-white border-green-600"
                                : "border-gray-300 text-gray-700 hover:bg-gray-50"
                            }`}
                            onClick={() =>
                              submitDecision.mutate({ entry_id: e.entry_id, decision: "keep" })
                            }
                          >
                            Keep
                          </button>
                          <button
                            className={`px-3 py-1 rounded text-xs font-medium border ${
                              d?.decision === "revoke"
                                ? "bg-red-600 text-white border-red-600"
                                : "border-gray-300 text-gray-700 hover:bg-gray-50"
                            }`}
                            onClick={() =>
                              submitDecision.mutate({ entry_id: e.entry_id, decision: "revoke" })
                            }
                          >
                            Revoke
                          </button>
                        </div>
                      ) : (
                        <span
                          className={`text-xs font-medium ${
                            d?.decision === "revoke" ? "text-red-600" : "text-green-600"
                          }`}
                        >
                          {d?.decision ?? "-"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-gray-500">
          {review.status === "collecting"
            ? "Collecting access data... this page will refresh automatically."
            : "No entries in this review."}
        </p>
      )}
    </div>
  );
}

// ─── Exports ──────────────────────────────────────────────────────────────────

export function AccessReviews() {
  const { id } = useParams<{ id?: string }>();
  return id ? <AccessReviewDetail /> : <AccessReviewList />;
}
