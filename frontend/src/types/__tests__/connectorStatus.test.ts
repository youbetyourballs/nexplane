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
