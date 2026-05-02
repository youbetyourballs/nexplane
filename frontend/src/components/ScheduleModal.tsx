import React, { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { ConnectorRead } from '../types/api';

const INTERVAL_OPTIONS = [
  { value: 1, label: 'Every hour' },
  { value: 6, label: 'Every 6 hours' },
  { value: 24, label: 'Every 24 hours' },
  { value: 168, label: 'Weekly' },
];

interface ScheduleData {
  action_id: string;
  interval_hours: number;
}

interface Props {
  connector: ConnectorRead;
  existing: ScheduleData | null;
  token: string;
  onClose: () => void;
}

export default function ScheduleModal({ connector, existing, token, onClose }: Props) {
  const [intervalHours, setIntervalHours] = useState(existing?.interval_hours ?? 24);
  const queryClient = useQueryClient();
  const headers = { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` };

  const saveMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/schedule`, {
        method: 'PUT',
        headers,
        body: JSON.stringify({ interval_hours: intervalHours, action_id: existing?.action_id ?? 'ingest' }),
      }).then(r => { if (!r.ok) throw new Error('Save failed'); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedule', connector.id] });
      onClose();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () =>
      fetch(`/connectors/${connector.id}/schedule`, { method: 'DELETE', headers }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedule', connector.id] });
      onClose();
    },
  });

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-sm p-6">
        <h2 className="text-lg font-semibold mb-4">Schedule Discovery &mdash; {connector.name}</h2>
        <div className="space-y-2 mb-6">
          {INTERVAL_OPTIONS.map(opt => (
            <label key={opt.value} className="flex items-center gap-2 cursor-pointer">
              <input
                type="radio"
                name="interval"
                value={opt.value}
                checked={intervalHours === opt.value}
                onChange={() => setIntervalHours(opt.value)}
                className="accent-indigo-600"
              />
              <span className="text-sm text-gray-700">{opt.label}</span>
            </label>
          ))}
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex-1 bg-indigo-600 text-white rounded-md py-2 text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
          >
            {saveMutation.isPending ? 'Saving...' : 'Save Schedule'}
          </button>
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600">
            Cancel
          </button>
        </div>
        {existing && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <button
              onClick={() => deleteMutation.mutate()}
              className="text-sm text-red-500 hover:text-red-700"
            >
              Remove schedule
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
