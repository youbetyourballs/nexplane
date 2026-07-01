// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import { useEffect, useState } from 'react';
import { useAuth } from '../hooks/useAuth';

interface DemoOrg {
  id: string;
  name: string;
  slug: string;
  admin_email: string;
  admin_password: string;
}

export function DemoOrgSwitcher() {
  const { user, login } = useAuth();
  const [orgs, setOrgs] = useState<DemoOrg[]>([]);

  useEffect(() => {
    fetch('/api/demo/orgs')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (data) setOrgs(data); })
      .catch(() => {});
  }, []);

  if (orgs.length === 0) return null;

  const currentOrgId = user?.organization_id ?? '';

  async function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const selected = orgs.find((o) => o.id === e.target.value);
    if (!selected || selected.id === currentOrgId) return;
    await login(selected.admin_email, selected.admin_password);
    window.location.reload();
  }

  return (
    <div className="px-3 py-2 border-t border-slate-700">
      <label className="block text-xs text-slate-400 mb-1">Demo org</label>
      <select
        value={currentOrgId}
        onChange={handleChange}
        className="w-full bg-slate-800 text-slate-200 text-sm rounded px-2 py-1 border border-slate-600 focus:outline-none focus:border-slate-400"
      >
        {orgs.map((org) => (
          <option key={org.id} value={org.id}>
            {org.name}
          </option>
        ))}
      </select>
    </div>
  );
}
