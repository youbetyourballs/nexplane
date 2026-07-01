// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// Mirror of backend app/tunnel/routing.py ROUTABLE_CONNECTOR_TYPES.
// Deferred/unwired types (nessus, openvas, wazuh, elastic, opnsense, teleport,
// infisical) are intentionally absent — Tasks 9/13 did not wire those clients.
export const ROUTABLE_CONNECTOR_TYPES = new Set([
  "postgres", "redis", "mongodb", "ssh", "winrm",
  "ldap", "active_directory", "freeipa",
  "gitlab", "gitea", "keycloak",
]);
