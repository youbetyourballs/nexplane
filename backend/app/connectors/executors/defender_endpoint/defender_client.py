# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Microsoft Defender for Endpoint API client."""
from __future__ import annotations
import httpx
from datetime import datetime, timezone, timedelta


class DefenderClient:
    BASE_URL = "https://api.securitycenter.microsoft.com/api"

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    async def _get_token(self) -> str:
        if self._token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._token
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "scope": "https://api.securitycenter.microsoft.com/.default",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600) - 60)
            return self._token

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._get_token()}", "Content-Type": "application/json"}

    async def list_machines(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(f"{self.BASE_URL}/machines", headers=await self._headers())
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def isolate_machine(self, machine_id: str, comment: str = "Nexplane automated isolation") -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/machines/{machine_id}/isolate",
                headers=await self._headers(),
                json={"Comment": comment, "IsolationType": "Full"},
            )
            resp.raise_for_status()
            return resp.json()

    async def unisolate_machine(self, machine_id: str, comment: str = "Nexplane automated unisolation") -> dict:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.BASE_URL}/machines/{machine_id}/unisolate",
                headers=await self._headers(),
                json={"Comment": comment},
            )
            resp.raise_for_status()
            return resp.json()


def get_defender_client(connector) -> DefenderClient | None:
    creds = getattr(connector, "credentials", None) or {}
    if not all(k in creds for k in ("tenant_id", "client_id", "client_secret")):
        return None
    return DefenderClient(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
