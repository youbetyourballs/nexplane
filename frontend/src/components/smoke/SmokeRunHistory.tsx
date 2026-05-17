import React from 'react';
import type { SmokeRun } from '../../api/smokeTestsApi';

interface Props {
  runs: SmokeRun[];
  onCancel: (id: string) => void;
}

export function SmokeRunHistory({ runs, onCancel }: Props) {
  const isOrphan = (run: SmokeRun) => {
    if (run.status !== 'running') return false;
    const age = Date.now() - new Date(run.started_at).getTime();
    return age > 2 * 60 * 60 * 1000;
  };

  if (runs.length === 0) return null;

  return (
    <div style={{ marginTop: '1.5rem' }}>
      <h3 style={{ fontSize: '0.875rem', fontWeight: 600, marginBottom: '0.75rem', color: '#374151' }}>
        Run History
      </h3>
      {runs.map(run => {
        const passCount = run.result_summary
          ? Object.values(run.result_summary).filter(r => r.status === 'passed').length
          : null;

        return (
          <div
            key={run.id}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.5rem',
              borderRadius: 4,
              fontSize: '0.8rem',
              borderBottom: '1px solid #f3f4f6',
            }}
          >
            <span style={{ color: '#6b7280', minWidth: 140 }}>
              {new Date(run.started_at).toLocaleString()}
            </span>
            <span style={{ minWidth: 80 }}>{run.phases.length} phases</span>
            <span style={{ minWidth: 80, fontWeight: 500 }}>{run.status}</span>
            {passCount !== null && (
              <span style={{ color: '#16a34a' }}>{passCount} passed</span>
            )}
            {isOrphan(run) && (
              <button
                onClick={() => onCancel(run.id)}
                style={{
                  marginLeft: 'auto',
                  padding: '0.25rem 0.5rem',
                  fontSize: '0.75rem',
                  background: '#fee2e2',
                  color: '#dc2626',
                  border: '1px solid #fca5a5',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
                title="Runner appears stuck — terminate EC2 instance"
              >
                Terminate orphan runner
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
