# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import httpx


class GiteaClient:
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token

    def _headers(self) -> dict:
        return {"Authorization": f"token {self.token}", "Content-Type": "application/json"}

    async def get_user(self, username: str) -> dict | None:
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{self.url}/api/v1/users/{username}", headers=self._headers())
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def suspend_user(self, username: str) -> dict:
        async with httpx.AsyncClient() as c:
            resp = await c.patch(f"{self.url}/api/v1/admin/users/{username}",
                headers=self._headers(), json={"login_name": username, "source_id": 0,
                                                "login_name_deprecated": username, "prohibit_login": True})
            resp.raise_for_status()
            return {"success": True, "username": username, "suspended": True}

    async def unsuspend_user(self, username: str) -> dict:
        async with httpx.AsyncClient() as c:
            resp = await c.patch(f"{self.url}/api/v1/admin/users/{username}",
                headers=self._headers(), json={"login_name": username, "source_id": 0,
                                                "prohibit_login": False})
            resp.raise_for_status()
            return {"success": True, "username": username, "suspended": False}

    async def list_deploy_keys(self, owner: str, repo: str) -> list:
        async with httpx.AsyncClient() as c:
            resp = await c.get(f"{self.url}/api/v1/repos/{owner}/{repo}/keys", headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def delete_deploy_key(self, owner: str, repo: str, key_id: int) -> dict:
        async with httpx.AsyncClient() as c:
            resp = await c.delete(f"{self.url}/api/v1/repos/{owner}/{repo}/keys/{key_id}",
                headers=self._headers())
            resp.raise_for_status()
            return {"success": True, "key_id": key_id, "deleted": True}


def get_gitea_client(connector) -> GiteaClient | None:
    creds = getattr(connector, "credentials", None) or {}
    url = creds.get("url") or creds.get("server_url")
    token = creds.get("token") or creds.get("api_token")
    if not url or not token:
        return None
    return GiteaClient(url=url, token=token)
