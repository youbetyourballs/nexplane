import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

export interface RunbookStepOut {
  id: string;
  step_number: number;
  name: string;
  type: string;
  change_type?: string;
  parameters?: Record<string, unknown>;
  asset_selector?: { tags: string[]; asset_ids: string[]; environment?: string };
  condition_expr?: string;
  on_true_step?: number;
  on_false_step?: number;
  prompt?: string;
  required_role?: string;
  timeout_hours?: number;
  on_timeout?: string;
  on_failure: string;
  parallel_steps: RunbookStepOut[];
}

export interface RunbookOut {
  id: string;
  organization_id: string;
  name: string;
  description?: string;
  version: number;
  tags: string[];
  is_seed: boolean;
  auto_execute: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  steps: RunbookStepOut[];
}

export interface RunbookStepResultOut {
  id: string;
  step_number: number;
  step_name: string;
  step_type: string;
  status: string;
  started_at?: string;
  completed_at?: string;
  change_request_ids: string[];
  result: Record<string, unknown>;
  error_message?: string;
}

export interface RunbookExecutionOut {
  id: string;
  runbook_id: string;
  runbook_version: number;
  triggered_by: string;
  triggered_at: string;
  completed_at?: string;
  context: Record<string, unknown>;
  status: string;
  current_step: number;
  step_results: RunbookStepResultOut[];
}

export const useRunbooks = (params?: { tag?: string; search?: string }) =>
  useQuery<RunbookOut[]>({
    queryKey: ["runbooks", params],
    queryFn: () =>
      apiClient.get<RunbookOut[]>("/api/runbooks", { params }).then((r) => r.data),
  });

export const useRunbook = (id: string) =>
  useQuery<RunbookOut>({
    queryKey: ["runbook", id],
    queryFn: () => apiClient.get<RunbookOut>(`/api/runbooks/${id}`).then((r) => r.data),
    enabled: !!id,
  });

export const useRunbookExecution = (id: string, poll: boolean) =>
  useQuery<RunbookExecutionOut>({
    queryKey: ["runbook-execution", id],
    queryFn: () =>
      apiClient.get<RunbookExecutionOut>(`/api/executions/${id}`).then((r) => r.data),
    refetchInterval: poll ? 5000 : false,
    enabled: !!id,
  });

export const useRunbookExecutions = (runbookId: string) =>
  useQuery<RunbookExecutionOut[]>({
    queryKey: ["runbook-executions", runbookId],
    queryFn: () =>
      apiClient
        .get<RunbookExecutionOut[]>(`/api/runbooks/${runbookId}/executions`)
        .then((r) => r.data),
    enabled: !!runbookId,
  });

export const useCreateRunbook = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<RunbookOut>) =>
      apiClient.post<RunbookOut>("/api/runbooks", data).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runbooks"] }),
  });
};

export const useUpdateRunbook = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<RunbookOut>) =>
      apiClient.put<RunbookOut>(`/api/runbooks/${id}`, data).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runbooks"] });
      qc.invalidateQueries({ queryKey: ["runbook", id] });
    },
  });
};

export const useForkRunbook = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      apiClient.post<RunbookOut>(`/api/runbooks/${id}/fork`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["runbooks"] }),
  });
};

export const useToggleRunbookAutoExecute = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (auto_execute: boolean) =>
      apiClient
        .put<RunbookOut>(`/api/runbooks/${id}`, { auto_execute })
        .then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["runbooks"] });
      qc.invalidateQueries({ queryKey: ["runbook", id] });
    },
  });
};

export const useTriggerRunbook = (id: string) =>
  useMutation({
    mutationFn: ({ context = {}, force = false }: { context?: Record<string, unknown>; force?: boolean }) =>
      apiClient
        .post<RunbookExecutionOut>(`/api/runbooks/${id}/trigger`, { context, force })
        .then((r) => r.data),
  });

export const useResumeCheckpoint = (executionId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ step_number, action }: { step_number: number; action: string }) =>
      apiClient
        .post<RunbookExecutionOut>(`/api/executions/${executionId}/resume`, {
          step_number,
          action,
        })
        .then((r) => r.data),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["runbook-execution", executionId] }),
  });
};

export const useAbortExecution = (executionId: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient
        .post<RunbookExecutionOut>(`/api/executions/${executionId}/abort`)
        .then((r) => r.data),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["runbook-execution", executionId] }),
  });
};
