"""HashiCorp Vault client using HVAC."""
from __future__ import annotations
import os


class VaultClient:
    def __init__(self, url: str, token: str | None = None,
                 role_id: str | None = None, secret_id: str | None = None,
                 namespace: str | None = None):
        self.url = url
        self.token = token
        self.role_id = role_id
        self.secret_id = secret_id
        self.namespace = namespace
        self._client = None

    def _get_client(self):
        if self._client:
            return self._client
        try:
            import hvac
            client = hvac.Client(url=self.url, namespace=self.namespace)
            if self.token:
                client.token = self.token
            elif self.role_id and self.secret_id:
                result = client.auth.approle.login(
                    role_id=self.role_id, secret_id=self.secret_id
                )
                client.token = result["auth"]["client_token"]
            self._client = client
            return client
        except ImportError:
            raise RuntimeError("hvac package not installed — pip install hvac")

    def read_secret(self, path: str, mount_point: str = "secret") -> dict:
        client = self._get_client()
        try:
            # KV v2
            result = client.secrets.kv.v2.read_secret_version(
                path=path, mount_point=mount_point
            )
            return result["data"]["data"]
        except Exception:
            # KV v1 fallback
            result = client.secrets.kv.read_secret(path=path, mount_point=mount_point)
            return result["data"]

    def write_secret(self, path: str, data: dict, mount_point: str = "secret") -> dict:
        client = self._get_client()
        try:
            result = client.secrets.kv.v2.create_or_update_secret(
                path=path, secret=data, mount_point=mount_point
            )
            return {"version": result.get("data", {}).get("version")}
        except Exception:
            client.secrets.kv.create_or_update_secret(
                path=path, secret=data, mount_point=mount_point
            )
            return {}

    def list_secrets(self, path: str, mount_point: str = "secret") -> list[str]:
        client = self._get_client()
        try:
            result = client.secrets.kv.v2.list_secrets(path=path, mount_point=mount_point)
            return result["data"]["keys"]
        except Exception:
            return []

    def rotate_database_credential(self, role_name: str, mount_point: str = "database") -> dict:
        """Trigger Vault to rotate a dynamic database credential."""
        client = self._get_client()
        result = client.secrets.database.rotate_static_role(name=role_name, mount_point=mount_point)
        return {"rotated": True, "role": role_name}

    def is_authenticated(self) -> bool:
        try:
            return self._get_client().is_authenticated()
        except Exception:
            return False


def get_vault_client(connector) -> VaultClient | None:
    creds = getattr(connector, "credentials", None) or {}
    url = creds.get("url") or creds.get("address") or creds.get("vault_addr")
    if not url:
        return None
    return VaultClient(
        url=url,
        token=creds.get("token") or creds.get("vault_token"),
        role_id=creds.get("role_id"),
        secret_id=creds.get("secret_id"),
        namespace=creds.get("namespace"),
    )
