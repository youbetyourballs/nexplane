"""Keycloak Admin REST API client."""
from __future__ import annotations
import httpx
from datetime import datetime, timezone


class KeycloakClient:
    def __init__(self, url: str, realm: str, client_id: str,
                 client_secret: str | None = None,
                 username: str | None = None, password: str | None = None):
        self.url = url.rstrip("/")
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    async def _get_token(self) -> str:
        if self._token and self._token_expiry and datetime.now(timezone.utc) < self._token_expiry:
            return self._token
        from datetime import timedelta
        data = {"client_id": self.client_id, "grant_type": "password" if self.username else "client_credentials"}
        if self.username:
            data.update({"username": self.username, "password": self.password})
        if self.client_secret:
            data["client_secret"] = self.client_secret
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/realms/{self.realm}/protocol/openid-connect/token", data=data)
            resp.raise_for_status()
            d = resp.json()
            self._token = d["access_token"]
            self._token_expiry = datetime.now(timezone.utc) + timedelta(seconds=d.get("expires_in", 300) - 30)
        return self._token

    async def _headers(self) -> dict:
        return {"Authorization": f"Bearer {await self._get_token()}", "Content-Type": "application/json"}

    async def get_user_by_username(self, username: str) -> dict | None:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.url}/admin/realms/{self.realm}/users",
                headers=await self._headers(),
                params={"username": username, "exact": "true"})
            resp.raise_for_status()
            users = resp.json()
            return users[0] if users else None

    async def disable_user(self, username: str) -> dict:
        user = await self.get_user_by_username(username)
        if not user:
            return {"success": False, "error": f"User {username} not found"}
        user_id = user["id"]
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.url}/admin/realms/{self.realm}/users/{user_id}",
                headers=await self._headers(),
                json={"enabled": False})
            resp.raise_for_status()
        return {"success": True, "user_id": user_id, "username": username, "action": "disabled"}

    async def enable_user(self, username: str) -> dict:
        user = await self.get_user_by_username(username)
        if not user:
            return {"success": False, "error": f"User {username} not found"}
        user_id = user["id"]
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.url}/admin/realms/{self.realm}/users/{user_id}",
                headers=await self._headers(),
                json={"enabled": True})
            resp.raise_for_status()
        return {"success": True, "user_id": user_id, "username": username, "action": "enabled"}

    async def revoke_sessions(self, username: str) -> dict:
        user = await self.get_user_by_username(username)
        if not user:
            return {"success": False, "error": f"User {username} not found"}
        user_id = user["id"]
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}/admin/realms/{self.realm}/users/{user_id}/sessions",
                headers=await self._headers())
        return {"success": True, "user_id": user_id, "sessions_revoked": True}

    async def create_user(self, username: str, email: str, password: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}/admin/realms/{self.realm}/users",
                headers=await self._headers(),
                json={
                    "username": username, "email": email, "enabled": True,
                    "credentials": [{"type": "password", "value": password, "temporary": False}]
                })
            if resp.status_code == 409:
                return {"success": False, "error": "User already exists"}
            resp.raise_for_status()
            user_id = resp.headers.get("Location", "").split("/")[-1]
            return {"success": True, "user_id": user_id, "username": username}

    async def delete_user(self, username: str) -> dict:
        user = await self.get_user_by_username(username)
        if not user:
            return {"success": False, "error": f"User {username} not found"}
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}/admin/realms/{self.realm}/users/{user['id']}",
                headers=await self._headers())
            resp.raise_for_status()
        return {"success": True, "username": username}


def get_keycloak_client(connector) -> KeycloakClient | None:
    creds = getattr(connector, "credentials", None) or {}
    url = creds.get("url") or creds.get("server_url")
    if not url:
        return None
    return KeycloakClient(
        url=url,
        realm=creds.get("realm", "master"),
        client_id=creds.get("client_id", "admin-cli"),
        client_secret=creds.get("client_secret"),
        username=creds.get("username") or creds.get("admin_username"),
        password=creds.get("password") or creds.get("admin_password"),
    )
