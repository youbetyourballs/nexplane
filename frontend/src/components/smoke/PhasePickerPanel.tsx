// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import React, { useState } from 'react';

const PHASE_GROUPS: Record<string, string[]> = {
  'Platform Orchestration': [
    'RUNBOOK_ONBOARDING', 'RUNBOOK_ACCOUNT_COMPROMISE', 'RUNBOOK_PATCH_CAMPAIGN',
  ],
  'Incident Response': [
    'IR_ISOLATE_HOST', 'IR_PRESERVE_EVIDENCE', 'IR_LOCKDOWN_ACCOUNT', 'IR_PHISHING_RESPONSE',
  ],
  'Platform Features': ['ACCESS_REVIEW', 'PROJECT_MICROSEG', 'VULN_PIPELINE'],
  'AWS Core': ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'W'],
  'Open Source Connectors': [
    'LDAP_ROTATE', 'VAULT_ROTATE', 'KEYCLOAK', 'K8S_RBAC', 'GITEA',
    'FREEIPA', 'GITLAB', 'TELEPORT', 'WAZUH_AGENT', 'FALCO_POLICY',
    'INFISICAL', 'POSTGRES_ROTATE', 'REDIS_ROTATE', 'MONGODB_ROTATE',
    'OPNSENSE_RULE', 'STEP_CA_ROTATE',
  ],
  'Scanners & SIEM': [
    'TRIVY_SCAN', 'LYNIS_AUDIT', 'SSL_EXPIRY',
    'OPENVAS_SCAN', 'NESSUS_SCAN', 'ELASTIC_ALERTS', 'SPLUNK_ALERTS',
  ],
  'Credential-Gated': ['OKTA_DISABLE', 'SERVICENOW_INCIDENT', 'PAGERDUTY_INCIDENT'],
  'New Phases': ['AD_DC_INTEGRITY', 'BIND_DNS', 'POLLER_BACKOFF', 'WINRM_BOOTSTRAP'],
};

const SUITES: Record<string, string[]> = {
  'Quick (IR + Runbooks)': [
    'IR_ISOLATE_HOST', 'IR_PRESERVE_EVIDENCE', 'RUNBOOK_ONBOARDING', 'RUNBOOK_ACCOUNT_COMPROMISE',
  ],
  'Full Suite': Object.values(PHASE_GROUPS).flat(),
  'Connectors Only': [
    ...PHASE_GROUPS['Open Source Connectors'],
    ...PHASE_GROUPS['Scanners & SIEM'],
    ...PHASE_GROUPS['Credential-Gated'],
  ],
  'Platform Features Only': [
    ...PHASE_GROUPS['Platform Orchestration'],
    ...PHASE_GROUPS['Incident Response'],
    ...PHASE_GROUPS['Platform Features'],
  ],
};

interface Props {
  onStart: (phases: string[]) => void;
  disabled: boolean;
}

export function PhasePickerPanel({ onStart, disabled }: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const toggle = (phase: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(phase) ? next.delete(phase) : next.add(phase);
      return next;
    });
  };

  const applySuite = (suiteName: string) => {
    setSelected(new Set(SUITES[suiteName] || []));
  };

  return (
    <div style={{ padding: '1rem', borderRight: '1px solid #e5e7eb', minWidth: 280 }}>
      <div style={{ marginBottom: '0.75rem', display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
        {Object.keys(SUITES).map(name => (
          <button
            key={name}
            onClick={() => applySuite(name)}
            style={{
              padding: '0.25rem 0.5rem',
              fontSize: '0.75rem',
              border: '1px solid #d1d5db',
              borderRadius: 4,
              cursor: 'pointer',
              background: 'white',
            }}
          >
            {name}
          </button>
        ))}
      </div>

      {Object.entries(PHASE_GROUPS).map(([group, phases]) => (
        <div key={group} style={{ marginBottom: '0.75rem' }}>
          <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#6b7280', marginBottom: '0.25rem' }}>
            {group}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
            {phases.map(phase => (
              <label key={phase} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.8rem', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selected.has(phase)}
                  onChange={() => toggle(phase)}
                />
                {phase}
              </label>
            ))}
          </div>
        </div>
      ))}

      <button
        onClick={() => onStart(Array.from(selected))}
        disabled={disabled || selected.size === 0}
        style={{
          width: '100%',
          padding: '0.5rem',
          marginTop: '0.75rem',
          background: disabled || selected.size === 0 ? '#9ca3af' : '#2563eb',
          color: 'white',
          border: 'none',
          borderRadius: 4,
          cursor: disabled || selected.size === 0 ? 'not-allowed' : 'pointer',
          fontWeight: 500,
        }}
      >
        Run {selected.size} phase{selected.size !== 1 ? 's' : ''}
      </button>
    </div>
  );
}
