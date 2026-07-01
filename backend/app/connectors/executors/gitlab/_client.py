# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

from app.connectors.executors.common.tunnel_http import tunnel_http_client


class GitLabClient:
    """GitLab REST API client.  Authenticates via personal access token
    passed in the PRIVATE-TOKEN header."""

    def __init__(self, url, token, connector=None):
        self.url = url.rstrip("/")
        self.token = token
        self._connector = connector

    def _headers(self):
        return {"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"}

    async def _http(self):
        return await tunnel_http_client(self._connector) if self._connector is not None else __import__("httpx").AsyncClient()

    async def get_user_by_username(self, username):
        async with await self._http() as c:
            resp = await c.get(f"{self.url}/api/v4/users",
                               headers=self._headers(),
                               params={"username": username})
            resp.raise_for_status()
            users = resp.json()
            return users[0] if users else None

    async def block_user(self, user_id):
        async with await self._http() as c:
            resp = await c.put(f"{self.url}/api/v4/users/{user_id}/block",
                               headers=self._headers())
            if resp.status_code not in (200, 201, 204):
                resp.raise_for_status()
            return {"success": True, "user_id": user_id, "action": "blocked"}

    async def unblock_user(self, user_id):
        async with await self._http() as c:
            resp = await c.put(f"{self.url}/api/v4/users/{user_id}/unblock",
                               headers=self._headers())
            if resp.status_code not in (200, 201, 204):
                resp.raise_for_status()
            return {"success": True, "user_id": user_id, "action": "unblocked"}

    async def list_personal_access_tokens(self, user_id):
        async with await self._http() as c:
            resp = await c.get(f"{self.url}/api/v4/personal_access_tokens",
                               headers=self._headers(),
                               params={"user_id": user_id, "state": "active"})
            resp.raise_for_status()
            return resp.json()

    async def revoke_personal_access_token(self, token_id):
        async with await self._http() as c:
            resp = await c.delete(f"{self.url}/api/v4/personal_access_tokens/{token_id}",
                                  headers=self._headers())
            if resp.status_code not in (200, 204):
                resp.raise_for_status()
            return {"success": True, "token_id": token_id, "revoked": True}

    async def create_personal_access_token(self, user_id, name, scopes=None):
        if scopes is None:
            scopes = ["api"]
        async with await self._http() as c:
            resp = await c.post(f"{self.url}/api/v4/users/{user_id}/personal_access_tokens",
                                headers=self._headers(),
                                json={"name": name, "scopes": scopes})
            resp.raise_for_status()
            return resp.json()


def get_gitlab_client(connector):
    creds = getattr(connector, "credentials", None) or {}
    url = creds.get("url") or creds.get("server_url")
    token = creds.get("token") or creds.get("api_token") or creds.get("private_token")
    if not url or not token:
        return None
    return GitLabClient(url=url, token=token, connector=connector)
