import hvac


class VaultKVBackend:
    """Vault KV v2 backend. Stores credentials at secret/data/nexplane/connectors/{connector_id}."""

    _MOUNT = "secret"

    def __init__(self, addr: str, token: str, _client=None):
        if _client is not None:
            self._client = _client
        else:
            self._client = hvac.Client(url=addr, token=token)

    def _kv_path(self, stored: str) -> str:
        # stored is "secret/data/nexplane/connectors/{id}" — extract the path component
        # Format: {mount}/data/{path}
        parts = stored.split("/data/", 1)
        return parts[1] if len(parts) == 2 else stored

    def encrypt_json(self, data: dict, *, connector_id: str = "") -> str:
        path = f"nexplane/connectors/{connector_id}"
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret=data,
            mount_point=self._MOUNT,
        )
        return f"{self._MOUNT}/data/{path}"

    def decrypt_json(self, stored: str) -> dict:
        path = self._kv_path(stored)
        resp = self._client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=self._MOUNT,
        )
        return resp["data"]["data"]

    def encrypt(self, value: str, *, connector_id: str = "") -> str:
        return self.encrypt_json({"v": value}, connector_id=connector_id)

    def decrypt(self, stored: str) -> str:
        return self.decrypt_json(stored)["v"]

    def delete_secret(self, stored: str) -> None:
        path = self._kv_path(stored)
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=path,
            mount_point=self._MOUNT,
        )
