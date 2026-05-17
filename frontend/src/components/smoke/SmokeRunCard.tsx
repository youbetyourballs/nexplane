import React, { useRef, useEffect } from 'react';
import { useSmokeStream } from '../../hooks/useSmokeStream';
import type { SmokeRun } from '../../api/smokeTestsApi';

interface Props {
  run: SmokeRun;
  active: boolean;
}

const STATUS_ICONS: Record<string, string> = {
  passed: '✅', failed: '❌', running: '⏳', pending: '○', skipped: '⚪',
};

export function SmokeRunCard({ run, active }: Props) {
  const { events } = useSmokeStream(active ? run.id : null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [events]);

  const phaseEvents = (phase: string) => events.filter(e => e.phase === phase);

  const phaseStatus = (phase: string): string => {
    const evts = phaseEvents(phase);
    if (evts.some(e => e.type === 'PHASE_PASS')) return 'passed';
    if (evts.some(e => e.type === 'PHASE_FAIL')) return 'failed';
    if (evts.some(e => e.type === 'PHASE_START')) return 'running';
    const stored = run.result_summary?.[phase];
    if (stored) return stored.status;
    return 'pending';
  };

  const elapsed = active
    ? Math.floor((Date.now() - new Date(run.started_at).getTime()) / 1000)
    : null;

  return (
    <div style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: '1rem', marginBottom: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
        <span style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: '#6b7280' }}>
          {run.id.slice(0, 8)}
        </span>
        <span style={{ fontSize: '0.8rem', fontWeight: 500 }}>{run.status}</span>
        <span style={{ fontSize: '0.8rem', color: '#6b7280' }}>
          {new Date(run.started_at).toLocaleString()}
        </span>
        {elapsed !== null && (
          <span style={{ fontSize: '0.8rem', color: '#2563eb' }}>{elapsed}s elapsed</span>
        )}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {run.phases.map(phase => {
          const status = phaseStatus(phase);
          const result = run.result_summary?.[phase];
          const logs = phaseEvents(phase);

          return (
            <details key={phase} open={status === 'running'} style={{ border: '1px solid #f3f4f6', borderRadius: 4, padding: '0.5rem' }}>
              <summary style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem' }}>
                <span>{STATUS_ICONS[status] || '○'}</span>
                <span style={{ fontWeight: 500 }}>{phase}</span>
                {result && <span style={{ color: '#9ca3af', fontSize: '0.75rem' }}>({result.duration_seconds}s)</span>}
              </summary>

              {result?.coverage_gaps && result.coverage_gaps.length > 0 && (
                <div style={{ marginTop: '0.5rem' }}>
                  {result.coverage_gaps.map(g => (
                    <div key={g} style={{ fontSize: '0.75rem', color: '#9ca3af' }}>⚪ {g}</div>
                  ))}
                </div>
              )}

              {active && logs.length > 0 && (
                <div
                  ref={logRef}
                  style={{
                    marginTop: '0.5rem',
                    maxHeight: 200,
                    overflow: 'auto',
                    fontFamily: 'monospace',
                    fontSize: '0.75rem',
                    background: '#f9fafb',
                    padding: '0.5rem',
                    borderRadius: 4,
                  }}
                >
                  {logs.map((e, i) => (
                    <div key={i}>
                      <span style={{ color: '#9ca3af' }}>{e.ts.slice(11, 19)} </span>
                      <span>{e.message}</span>
                    </div>
                  ))}
                </div>
              )}
            </details>
          );
        })}
      </div>
    </div>
  );
}
