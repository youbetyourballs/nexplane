# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Infisical REST API client using httpx."""
from typing import Optional
import httpx


class InfisicalClient:
    def __init__(self, base_url: str, token: str, proxy: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = httpx.Client(timeout=30.0, **({"proxy": proxy} if proxy else {}))

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def list_secrets(self, workspace_id: str, environment: str) -> list:
        resp = self._http.get(
            f"{self.base_url}/api/v3/secrets/",
            headers=self._headers(),
            params={"workspaceId": workspace_id, "environment": environment},
        )
        resp.raise_for_status()
        return resp.json().get("secrets", [])

    def get_secret(self, workspace_id: str, environment: str, secret_name: str) -> dict:
        resp = self._http.get(
            f"{self.base_url}/api/v3/secrets/{secret_name}",
            headers=self._headers(),
            params={"workspaceId": workspace_id, "environment": environment},
        )
        resp.raise_for_status()
        return resp.json().get("secret", {})

    def create_secret(self, workspace_id: str, environment: str,
                      secret_name: str, secret_value: str) -> dict:
        resp = self._http.post(
            f"{self.base_url}/api/v3/secrets/{secret_name}",
            headers=self._headers(),
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "secretValue": secret_value,
            },
        )
        resp.raise_for_status()
        return resp.json().get("secret", {})

    def update_secret(self, workspace_id: str, environment: str,
                      secret_name: str, secret_value: str) -> dict:
        resp = self._http.patch(
            f"{self.base_url}/api/v3/secrets/{secret_name}",
            headers=self._headers(),
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "secretValue": secret_value,
            },
        )
        resp.raise_for_status()
        return resp.json().get("secret", {})


async def get_infisical_client(connector) -> Optional[InfisicalClient]:
    from app.tunnel.routing import http_proxy
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url") or creds.get("url")
    token = creds.get("token") or creds.get("api_token")
    if not base_url or not token:
        return None
    proxy = await http_proxy(connector)
    return InfisicalClient(base_url=base_url, token=token, proxy=proxy)
