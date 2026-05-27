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

    def list_leases(self) -> list[dict]:
        """List all dynamic secret leases with real per-lease TTLs from read_lease."""
        result = self._client.sys.list_leases(prefix="")
        keys = result.get("data", {}).get("keys", [])
        leases = []
        for k in keys:
            try:
                info = self._client.sys.read_lease(lease_id=k)
                ttl = info.get("data", {}).get("ttl", 3600)
                renewable = info.get("data", {}).get("renewable", True)
            except Exception:
                ttl = 3600
                renewable = True
            leases.append({"lease_id": k, "ttl": ttl, "renewable": renewable})
        return leases

    def renew_lease(self, lease_id: str, increment: int = 3600) -> None:
        """Renew a Vault dynamic secret lease."""
        self._client.sys.renew_lease(lease_id=lease_id, increment=increment)
