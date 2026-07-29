# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    connector_type = parameters["connector_type"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "discover_account",
            "connector_type": connector_type,
            "target_email": target_email,
            "found": False,
            "account_identifier": None,
            "details": {},
            "simulated": True,
        }

    handler = _HANDLERS.get(connector_type)
    if handler is None:
        return {
            "action": "discover_account",
            "connector_type": connector_type,
            "target_email": target_email,
            "found": False,
            "account_identifier": None,
            "details": {"error": f"No discovery handler for {connector_type}"},
        }

    return await handler(target_email, creds)


async def _discover_ad(email: str, creds: dict) -> dict:
    from app.connectors.executors.active_directory._client import get_connection

    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(base_dn, f"(mail={email})", attributes=["sAMAccountName", "displayName", "userAccountControl"])
        if not conn.entries:
            conn.search(base_dn, f"(userPrincipalName={email})", attributes=["sAMAccountName", "displayName", "userAccountControl"])
        if not conn.entries:
            conn.unbind()
            return None, {}
        entry = conn.entries[0]
        sam = entry.sAMAccountName.value
        details = {
            "displayName": str(entry.displayName) if entry.displayName else None,
            "userAccountControl": int(entry.userAccountControl.value),
        }
        conn.unbind()
        return sam, details

    sam, details = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "discover_account",
        "connector_type": "active_directory",
        "target_email": email,
        "found": sam is not None,
        "account_identifier": sam,
        "details": details,
    }


async def _discover_okta(email: str, creds: dict) -> dict:
    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.get(f"/api/v1/users/{email}", headers=headers)
        if resp.status_code == 404:
            return {"action": "discover_account", "connector_type": "okta", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        resp.raise_for_status()
        user = resp.json()

    return {
        "action": "discover_account",
        "connector_type": "okta",
        "target_email": email,
        "found": True,
        "account_identifier": user["id"],
        "details": {"status": user.get("status"), "login": user.get("profile", {}).get("login")},
    }


async def _discover_entra(email: str, creds: dict) -> dict:
    import httpx
    tenant_id = creds.get("tenant_id")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": client_id,
                  "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"},
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{email}",
            headers=headers,
            params={"$select": "id,displayName,accountEnabled"},
        )
        if resp.status_code == 404:
            return {"action": "discover_account", "connector_type": "entra_id", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        resp.raise_for_status()
        user = resp.json()

    return {
        "action": "discover_account",
        "connector_type": "entra_id",
        "target_email": email,
        "found": True,
        "account_identifier": user["id"],
        "details": {"displayName": user.get("displayName"), "accountEnabled": user.get("accountEnabled")},
    }


async def _discover_google(email: str, creds: dict) -> dict:
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_info = json.loads(creds.get("service_account_json", "{}"))
    admin_email = creds.get("admin_email")
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
        subject=admin_email,
    )

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        try:
            user = service.users().get(userKey=email).execute()
            return user
        except Exception as exc:
            if "404" in str(exc) or "Resource Not Found" in str(exc):
                return None
            raise

    user = await asyncio.get_event_loop().run_in_executor(None, _sync)
    if user is None:
        return {"action": "discover_account", "connector_type": "google_workspace", "target_email": email, "found": False, "account_identifier": None, "details": {}}

    return {
        "action": "discover_account",
        "connector_type": "google_workspace",
        "target_email": email,
        "found": True,
        "account_identifier": user["primaryEmail"],
        "details": {"suspended": user.get("suspended"), "fullName": user.get("name", {}).get("fullName")},
    }


async def _discover_github(email: str, creds: dict) -> dict:
    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        resp = await client.get(f"/search/users?q={email}+in:email", headers=headers)
        if resp.status_code != 200:
            return {"action": "discover_account", "connector_type": "github", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        items = resp.json().get("items", [])
        if not items:
            return {"action": "discover_account", "connector_type": "github", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        login = items[0]["login"]

        # Verify they are actually in this org
        member_resp = await client.get(f"/orgs/{org}/members/{login}", headers=headers)
        if member_resp.status_code != 204:
            return {"action": "discover_account", "connector_type": "github", "target_email": email, "found": False, "account_identifier": None, "details": {"reason": "not an org member"}}

    return {
        "action": "discover_account",
        "connector_type": "github",
        "target_email": email,
        "found": True,
        "account_identifier": login,
        "details": {"login": login, "org": org},
    }


async def _discover_slack(email: str, creds: dict) -> dict:
    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        resp = await client.get("/users.lookupByEmail", headers=headers, params={"email": email})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return {"action": "discover_account", "connector_type": "slack", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        user = data["user"]

    return {
        "action": "discover_account",
        "connector_type": "slack",
        "target_email": email,
        "found": True,
        "account_identifier": user["id"],
        "details": {"name": user.get("name"), "deleted": user.get("deleted", False)},
    }


async def _discover_crowdstrike(email: str, creds: dict) -> dict:
    import httpx
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    base_url = creds.get("base_url", "https://api.crowdstrike.com")
    username = email.split("@")[0]

    async with httpx.AsyncClient(base_url=base_url) as client:
        token_resp = await client.post("/oauth2/token", data={"client_id": client_id, "client_secret": client_secret})
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/devices/queries/devices/v1",
            headers=headers,
            params={"filter": f"last_login_user:'{username}'"},
        )
        resp.raise_for_status()
        device_ids = resp.json().get("resources", [])

    found = len(device_ids) > 0
    return {
        "action": "discover_account",
        "connector_type": "crowdstrike",
        "target_email": email,
        "found": found,
        "account_identifier": ",".join(device_ids) if found else None,
        "details": {"device_count": len(device_ids), "device_ids": device_ids},
    }


_HANDLERS = {
    "active_directory": _discover_ad,
    "okta": _discover_okta,
    "entra_id": _discover_entra,
    "google_workspace": _discover_google,
    "github": _discover_github,
    "slack": _discover_slack,
    "crowdstrike": _discover_crowdstrike,
}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "discover_account_rollback", "skipped": True, "reason": "discovery is read-only"}
