// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useEffect, useState } from 'react';

export interface ProgressEvent {
  ts: string;
  type: 'PHASE_START' | 'STEP' | 'CONNECTOR_SKIP' | 'PHASE_PASS' | 'PHASE_FAIL';
  phase: string;
  message: string;
}

export function useSmokeStream(runId: string | null) {
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!runId) return;
    const baseUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
    const token = localStorage.getItem('access_token');
    const url = `${baseUrl}/smoke-tests/runs/${runId}/stream${token ? `?token=${token}` : ''}`;
    const es = new EventSource(url);

    es.onopen = () => setConnected(true);
    es.onmessage = (e) => {
      if (e.data === '{}' && e.lastEventId === 'done') {
        es.close();
        setConnected(false);
        return;
      }
      try {
        const event: ProgressEvent = JSON.parse(e.data);
        setEvents(prev => [...prev, event]);
      } catch {}
    };
    es.addEventListener('done', () => { es.close(); setConnected(false); });
    es.onerror = () => { es.close(); setConnected(false); };

    return () => { es.close(); setConnected(false); };
  }, [runId]);

  return { events, connected };
}
