# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Infisical REST API client using httpx."""
from typing import Optional
import httpx


class InfisicalClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def list_secrets(self, workspace_id: str, environment: str) -> list:
        resp = httpx.get(
            f"{self.base_url}/api/v3/secrets/",
            headers=self._headers(),
            params={"workspaceId": workspace_id, "environment": environment},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("secrets", [])

    def get_secret(self, workspace_id: str, environment: str, secret_name: str) -> dict:
        resp = httpx.get(
            f"{self.base_url}/api/v3/secrets/{secret_name}",
            headers=self._headers(),
            params={"workspaceId": workspace_id, "environment": environment},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("secret", {})

    def create_secret(self, workspace_id: str, environment: str,
                      secret_name: str, secret_value: str) -> dict:
        resp = httpx.post(
            f"{self.base_url}/api/v3/secrets/{secret_name}",
            headers=self._headers(),
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "secretValue": secret_value,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("secret", {})

    def update_secret(self, workspace_id: str, environment: str,
                      secret_name: str, secret_value: str) -> dict:
        resp = httpx.patch(
            f"{self.base_url}/api/v3/secrets/{secret_name}",
            headers=self._headers(),
            json={
                "workspaceId": workspace_id,
                "environment": environment,
                "secretValue": secret_value,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("secret", {})


def get_infisical_client(connector) -> Optional[InfisicalClient]:
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url") or creds.get("url")
    token = creds.get("token") or creds.get("api_token")
    if not base_url or not token:
        return None
    return InfisicalClient(base_url=base_url, token=token)
