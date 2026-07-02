# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Wazuh REST API client using httpx."""
from typing import Optional
import httpx


class WazuhClient:
    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False, proxy: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._token: Optional[str] = None
        self._proxy = proxy
        self._http = httpx.Client(verify=self.verify_ssl, timeout=30.0,
                                   **({"proxy": proxy} if proxy else {}))

    def get_token(self) -> str:
        """Authenticate with basic auth, return JWT token."""
        resp = self._http.post(
            f"{self.base_url}/security/user/authenticate",
            auth=(self.username, self.password),
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["data"]["token"]
        return self._token

    def _auth_headers(self) -> dict:
        if not self._token:
            self.get_token()
        return {"Authorization": f"Bearer {self._token}"}

    def list_agents(self) -> list:
        resp = self._http.get(
            f"{self.base_url}/agents",
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("affected_items", [])

    def get_agent(self, agent_id: str) -> dict:
        resp = self._http.get(
            f"{self.base_url}/agents/{agent_id}",
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("affected_items", [])
        return items[0] if items else {}

    def register_agent(self, name: str) -> dict:
        resp = self._http.post(
            f"{self.base_url}/agents",
            headers=self._auth_headers(),
            json={"name": name},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def delete_agent(self, agent_id: str) -> dict:
        resp = self._http.delete(
            f"{self.base_url}/agents",
            headers=self._auth_headers(),
            params={"agents_list": agent_id, "status": "all", "older_than": "0s"},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})


async def get_wazuh_client(connector) -> Optional[WazuhClient]:
    from app.tunnel.routing import http_proxy
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url") or creds.get("url")
    username = creds.get("username") or creds.get("user", "wazuh-wui")
    password = creds.get("password")
    if not base_url or not password:
        return None
    proxy = await http_proxy(connector)
    return WazuhClient(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=creds.get("verify_ssl", False),
        proxy=proxy,
    )
