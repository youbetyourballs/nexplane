// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

import type { ConnectorStatus } from "../api";

const statuses: ConnectorStatus[] = [
  "active",
  "error",
  "syncing",
  "credential_expired",
  "never_synced",
  "disabled",
];

test("ConnectorStatus type includes all 6 states", () => {
  expect(statuses).toHaveLength(6);
});
