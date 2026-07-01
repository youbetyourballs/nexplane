# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import hvac


def get_vault_client(creds: dict) -> hvac.Client:
    kwargs = {"url": creds["vault_addr"]}
    if creds.get("namespace"):
        kwargs["namespace"] = creds["namespace"]

    client = hvac.Client(**kwargs)

    if creds.get("token"):
        client.token = creds["token"]
    elif creds.get("role_id") and creds.get("secret_id"):
        result = client.auth.approle.login(role_id=creds["role_id"], secret_id=creds["secret_id"])
        client.token = result["auth"]["client_token"]
    else:
        raise ValueError("Either token or role_id+secret_id must be provided")

    return client


class VaultClient:
    """Thin wrapper around hvac.Client with lease management helpers."""

    def __init__(self, hvac_client: hvac.Client) -> None:
        self._client = hvac_client

    @classmethod
    def from_connector(cls, connector) -> "VaultClient":
        creds = getattr(connector, "credentials", {}) or {}
        hvac_client = get_vault_client(creds)
        return cls(hvac_client)

    def _collect_lease_ids(self, prefix: str = "") -> list[str]:
        """Recursively collect all leaf lease IDs under prefix."""
        try:
            result = self._client.sys.list_leases(prefix=prefix)
        except Exception:
            return []
        keys = result.get("data", {}).get("keys", [])
        lease_ids = []
        for k in keys:
            full = prefix + k
            if k.endswith("/"):
                lease_ids.extend(self._collect_lease_ids(full))
            else:
                lease_ids.append(full)
        return lease_ids

    def list_leases(self) -> list[dict]:
        """List all dynamic secret leases with real per-lease TTLs via recursive traversal."""
        lease_ids = self._collect_lease_ids("")
        leases = []
        for lease_id in lease_ids:
            try:
                info = self._client.sys.read_lease(lease_id=lease_id)
                data = info.get("data", {})
                ttl = data.get("ttl")
                renewable = data.get("renewable", True)
                # ttl=0 means expired or non-renewable; still report it
                leases.append({"lease_id": lease_id, "ttl": ttl if ttl is not None else 0,
                               "renewable": renewable})
            except Exception:
                # Lease may have expired between list and read — skip it
                pass
        return leases

    def renew_lease(self, lease_id: str, increment: int = 3600) -> None:
        """Renew a Vault dynamic secret lease."""
        self._client.sys.renew_lease(lease_id=lease_id, increment=increment)
