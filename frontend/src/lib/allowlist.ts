// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

export const RFC1918_DEFAULT_ALLOWLIST = ["10.0.0.0/8:*", "172.16.0.0/12:*", "192.168.0.0/16:*"];

// Validate one "host:port" allowlist entry against the backend authorizer grammar.
// Returns null when valid, else an error message.
export function validateAllowlistEntry(entry: string): string | null {
  const e = entry.trim();
  const idx = e.lastIndexOf(":");
  if (idx <= 0 || idx === e.length - 1) return "Must be host:port";
  const host = e.slice(0, idx);
  const port = e.slice(idx + 1);
  if (!host) return "Missing host";
  const portOk =
    port === "*" ||
    /^\d{1,5}$/.test(port) && +port >= 1 && +port <= 65535 ||
    /^\d{1,5}-\d{1,5}$/.test(port) &&
      (() => { const [lo, hi] = port.split("-").map(Number); return lo >= 1 && lo <= hi && hi <= 65535; })();
  if (!portOk) return "Invalid port (1-65535, range, or *)";
  return null;
}
