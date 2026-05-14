"""Microsoft Graph API client for Azure AD / Entra ID operations."""
from __future__ import annotations
import httpx
from datetime import datetime, timezone


class AzureADClient:
    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

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
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            from datetime import timedelta
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=data.get("expires_in", 3600) - 60)
            return self._token

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._get_token()}", "Content-Type": "application/json"}

    async def get_user(self, user_id_or_upn: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.GRAPH_BASE}/users/{user_id_or_upn}",
                headers=await self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def disable_user(self, user_id_or_upn: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self.GRAPH_BASE}/users/{user_id_or_upn}",
                headers=await self._headers(),
                json={"accountEnabled": False},
            )
            resp.raise_for_status()
            return {"user": user_id_or_upn, "accountEnabled": False, "action": "disable"}

    async def enable_user(self, user_id_or_upn: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{self.GRAPH_BASE}/users/{user_id_or_upn}",
                headers=await self._headers(),
                json={"accountEnabled": True},
            )
            resp.raise_for_status()
            return {"user": user_id_or_upn, "accountEnabled": True, "action": "enable"}

    async def create_user(self, display_name: str, upn: str, password: str, force_change_password: bool = True) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.GRAPH_BASE}/users",
                headers=await self._headers(),
                json={
                    "displayName": display_name,
                    "userPrincipalName": upn,
                    "accountEnabled": True,
                    "passwordProfile": {
                        "forceChangePasswordNextSignIn": force_change_password,
                        "password": password,
                    },
                    "mailNickname": upn.split("@")[0],
                },
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_user(self, user_id_or_upn: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.GRAPH_BASE}/users/{user_id_or_upn}",
                headers=await self._headers(),
            )
            resp.raise_for_status()
            return {"user": user_id_or_upn, "action": "deleted"}

    async def revoke_sessions(self, user_id_or_upn: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.GRAPH_BASE}/users/{user_id_or_upn}/revokeSignInSessions",
                headers=await self._headers(),
            )
            resp.raise_for_status()
            return {"user": user_id_or_upn, "action": "sessions_revoked"}


def get_azure_ad_client(connector) -> AzureADClient | None:
    creds = getattr(connector, "credentials", None) or {}
    if not all(k in creds for k in ("tenant_id", "client_id", "client_secret")):
        return None
    return AzureADClient(
        tenant_id=creds["tenant_id"],
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
    )
