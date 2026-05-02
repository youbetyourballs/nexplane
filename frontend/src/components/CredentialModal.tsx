import React, { useState, useEffect } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ConnectorRead, CredentialField, CredentialStatus } from '../types/api';

interface Props {
  connector: ConnectorRead;
  token: string;
  onClose: () => void;
}

export default function CredentialModal({ connector, token, onClose }: Props) {
  const queryClient = useQueryClient();
  const headers = { Authorization: `Bearer ${token}` };

  const { data: credStatus, isLoading } = useQuery<CredentialStatus>({
    queryKey: ['credentials', connector.id],
    queryFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, { headers }).then(r => r.json()),
  });

  const fields: CredentialField[] = credStatus?.fields ?? [];
  const [values, setValues] = useState<Record<string, string>>({});

  useEffect(() => {
    if (fields.length > 0) {
      setValues(Object.fromEntries(fields.map(f => [f.name, f.default ?? ''])));
    }
  }, [fields.length]);

  const saveMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, {
        method: 'PUT',
        headers: { ...headers, 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials: values }),
      }).then(r => { if (!r.ok) throw new Error('Save failed'); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credentials', connector.id] });
      onClose();
    },
  });

  const clearMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/credentials`, { method: 'DELETE', headers })
        .then(r => { if (!r.ok && r.status !== 204) throw new Error('Delete failed'); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['credentials', connector.id] });
      onClose();
    },
  });

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
        <div className="bg-white rounded-lg p-8">Loading...</div>
      </div>
    );
  }

  if (!fields.length) return null;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6">
        <h2 className="text-lg font-semibold mb-1">Configure Credentials</h2>
        <p className="text-sm text-gray-500 mb-4">{connector.name}</p>

        <div className="space-y-3">
          {fields.map(field => (
            <div key={field.name}>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {field.label}
                {field.required && <span className="text-red-500 ml-1">*</span>}
              </label>
              <input
                type={field.type === 'password' ? 'password' : 'text'}
                value={values[field.name] ?? ''}
                onChange={e => setValues(prev => ({ ...prev, [field.name]: e.target.value }))}
                placeholder={field.default ?? ''}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          ))}
        </div>

        {saveMutation.isError && (
          <p className="text-sm text-red-500 mt-2">Failed to save. Check all required fields.</p>
        )}

        <div className="flex gap-3 mt-6">
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex-1 bg-indigo-600 text-white rounded-md py-2 text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {saveMutation.isPending ? 'Saving...' : 'Save Credentials'}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">
            Cancel
          </button>
        </div>

        {credStatus?.configured && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <button
              onClick={() => clearMutation.mutate()}
              disabled={clearMutation.isPending}
              className="text-sm text-red-500 hover:text-red-700"
            >
              Clear credentials
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
