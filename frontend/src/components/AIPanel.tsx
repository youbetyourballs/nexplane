import { useState, useRef, useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, X, Plus, FileText } from "lucide-react";
import { projectsApi, changeRequestsApi } from "../api/endpoints";
import type { AIProposedCR, ChangeType } from "../types/api";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface AIPanelProps {
  projectId: string;
  projectGoal: string;
  initialConversation: Message[];
  onClose: () => void;
}

export function AIPanel({ projectId, projectGoal, initialConversation, onClose }: AIPanelProps) {
  const qc = useQueryClient();
  const [messages, setMessages] = useState<Message[]>(initialConversation);
  const [input, setInput] = useState("");
  const [proposedCRs, setProposedCRs] = useState<AIProposedCR[] | null>(null);
  const [addedIndices, setAddedIndices] = useState<Set<number>>(new Set());
  const [showPreview, setShowPreview] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const { data: previewData, isLoading: isLoadingPreview, isError: isPreviewError } = useQuery({
    queryKey: ["prompt-preview", projectId, showPreview ? input : ""],
    queryFn: () => projectsApi.getPromptPreview(projectId, input || undefined),
    enabled: showPreview,
    staleTime: 10_000,
  });

  const chatMutation = useMutation({
    mutationFn: (message: string) =>
      projectsApi.aiChat(projectId, { message }),
    onSuccess: (data) => {
      setMessages((prev) => [...prev, { role: "assistant", content: data.reply }]);
      if (data.proposed_crs) {
        setProposedCRs(data.proposed_crs);
      }
    },
  });

  function handleSend() {
    const msg = input.trim();
    if (!msg || chatMutation.isPending) return;
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setInput("");
    chatMutation.mutate(msg);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const addCRMutation = useMutation({
    mutationFn: async ({ cr, idx }: { cr: AIProposedCR; idx: number }) => {
      const newCr = await changeRequestsApi.create({
        title: cr.title,
        change_type: cr.change_type as ChangeType,
        target_asset_ids: cr.target_asset_ids ?? [],
        desired_outcome: cr.desired_outcome ?? cr.desired_outcome_sketch ?? {},
      });
      await projectsApi.addMember(projectId, { change_request_id: newCr.id });
      return idx;
    },
    onSuccess: (idx) => {
      setAddedIndices((prev) => new Set([...prev, idx]));
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
    },
  });

  const addAllMutation = useMutation({
    mutationFn: async () => {
      if (!proposedCRs) return;
      for (let i = 0; i < proposedCRs.length; i++) {
        if (!addedIndices.has(i)) {
          const cr = proposedCRs[i];
          const newCr = await changeRequestsApi.create({
            title: cr.title,
            change_type: cr.change_type as ChangeType,
            target_asset_ids: cr.target_asset_ids ?? [],
            desired_outcome: cr.desired_outcome ?? cr.desired_outcome_sketch ?? {},
          });
          await projectsApi.addMember(projectId, { change_request_id: newCr.id });
          setAddedIndices((prev) => new Set([...prev, i]));
        }
      }
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      qc.invalidateQueries({ queryKey: ["change-requests"] });
    },
    onError: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  return (
    <div className="flex flex-col h-full bg-white border border-slate-200 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
        <span className="text-sm font-semibold text-slate-900">✦ AI Assistant</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowPreview((v) => !v)}
            title="Toggle prompt preview"
            className={`p-1 rounded transition-colors ${showPreview ? "text-brand-600 bg-brand-50" : "text-slate-400 hover:text-slate-600"}`}
          >
            <FileText className="w-4 h-4" />
          </button>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Body — chat on left, optional preview on right */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Chat column */}
        <div className="flex flex-col flex-1 min-w-0 min-h-0">
          {/* Conversation */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
            {messages.length === 0 && (
              <div className="text-sm text-slate-400 text-center py-8">
                Describe your goal above, then ask the AI to help plan the change requests.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-xs rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                    m.role === "user" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-900"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {chatMutation.isPending && (
              <div className="flex justify-start">
                <div className="bg-slate-100 rounded-lg px-3 py-2 text-sm text-slate-400 animate-pulse">
                  Thinking…
                </div>
              </div>
            )}
            {chatMutation.isError && (
              <div className="text-xs text-red-500 text-center">
                {(chatMutation.error as { response?: { status?: number } })?.response?.status === 402
                  ? "AI not configured. Ask an admin to add an Anthropic API key in Settings."
                  : "Something went wrong. Please try again."}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Proposed CRs */}
          {proposedCRs && proposedCRs.length > 0 && (
            <div className="border-t border-slate-100 p-3 space-y-2 shrink-0">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                Proposed Changes
              </p>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {proposedCRs.map((cr, i) => (
                  <div
                    key={i}
                    className="flex items-start justify-between gap-2 bg-slate-50 rounded-md px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        {cr.seq != null && (
                          <span className="text-xs font-mono text-slate-400 shrink-0">{cr.seq}.</span>
                        )}
                        <span className="text-xs font-medium text-slate-900 truncate">{cr.title}</span>
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5">
                        {cr.change_type.replace(/_/g, " ")}
                        {cr.target_assets?.length > 0 && (
                          <> · {cr.target_assets.slice(0, 2).join(", ")}{cr.target_assets.length > 2 ? ` +${cr.target_assets.length - 2}` : ""}</>
                        )}
                        {cr.depends_on?.length > 0 && (
                          <> · <span className="text-amber-600">after {cr.depends_on.join(", ")}</span></>
                        )}
                        {cr.notes && <> · {cr.notes}</>}
                      </div>
                    </div>
                    {addedIndices.has(i) ? (
                      <span className="text-xs text-emerald-600 shrink-0">✓ Added</span>
                    ) : (
                      <button
                        onClick={() => addCRMutation.mutate({ cr, idx: i })}
                        disabled={addCRMutation.isPending}
                        className="shrink-0 p-1 text-brand-600 hover:bg-brand-50 rounded"
                      >
                        <Plus className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              {addAllMutation.isError && (
                <p className="text-xs text-red-500">Some items could not be added. Check the project and try again.</p>
              )}
              {proposedCRs.some((_, i) => !addedIndices.has(i)) && (
                <button
                  onClick={() => addAllMutation.mutate()}
                  disabled={addAllMutation.isPending}
                  className="w-full text-xs text-brand-600 hover:underline py-1 disabled:opacity-50"
                >
                  {addAllMutation.isPending ? "Adding…" : "Add all to Project"}
                </button>
              )}
            </div>
          )}

          {/* Input */}
          <div className="border-t border-slate-100 p-3 flex gap-2 shrink-0">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask the AI to help plan your project…"
              rows={2}
              className="flex-1 text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || chatMutation.isPending}
              className="self-end p-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Preview panel */}
        {showPreview && (
          <div className="w-72 border-l border-slate-100 flex flex-col min-h-0 bg-slate-50">
            <div className="px-3 py-2 border-b border-slate-100 shrink-0">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Full Prompt</span>
            </div>
            <div className="flex-1 overflow-y-auto p-3 min-h-0">
              {isLoadingPreview ? (
                <p className="text-xs text-slate-400 animate-pulse">Loading…</p>
              ) : isPreviewError ? (
                <p className="text-xs text-red-500">Failed to load preview.</p>
              ) : previewData?.prompt ? (
                <pre className="text-xs text-slate-600 whitespace-pre-wrap font-mono leading-relaxed">
                  {previewData.prompt}
                </pre>
              ) : (
                <p className="text-xs text-slate-400">No preview available.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
