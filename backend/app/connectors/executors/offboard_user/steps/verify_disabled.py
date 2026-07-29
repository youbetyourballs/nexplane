# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

# Exposed as module-level so tests can monkeypatch it
_DELAYS = [2, 4, 8, 16, 30]


async def execute(parameters: dict, connector) -> dict:
    connector_type = parameters["connector_type"]
    account_identifier = parameters["account_identifier"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "verify_disabled",
            "connector_type": connector_type,
            "verified": True,
            "simulated": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }

    checker = _CHECKERS.get(connector_type)
    if checker is None:
        return {
            "action": "verify_disabled",
            "connector_type": connector_type,
            "verified": False,
            "error": f"No verification handler for {connector_type}",
        }

    # Immediate check first, then retry with delays
    for attempt, delay in enumerate(([0] + _DELAYS), start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            final_state = await checker(account_identifier, creds)
        except Exception as exc:
            return {
                "action": "verify_disabled",
                "connector_type": connector_type,
                "account_identifier": account_identifier,
                "verified": False,
                "attempts": attempt,
                "error": str(exc),
            }
        if final_state.get("is_disabled"):
            return {
                "action": "verify_disabled",
                "connector_type": connector_type,
                "account_identifier": account_identifier,
                "verified": True,
                "attempts": attempt,
                "final_state": final_state,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }

    return {
        "action": "verify_disabled",
        "connector_type": connector_type,
        "account_identifier": account_identifier,
        "verified": False,
        "attempts": len(_DELAYS) + 1,
        "final_state": final_state,
        "error": "Account still active after 60s retry window",
    }


async def _check_ad(sam: str, creds: dict) -> dict:
    from app.connectors.executors.active_directory._client import get_connection

    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(base_dn, f"(sAMAccountName={sam})", attributes=["userAccountControl"])
        if not conn.entries:
            conn.unbind()
            return None
        uac = int(conn.entries[0].userAccountControl.value)
        conn.unbind()
        return uac

    uac = await asyncio.get_event_loop().run_in_executor(None, _sync)
    if uac is None:
        return {"is_disabled": False, "error": f"User {sam} not found in AD"}
    return {"is_disabled": bool(uac & 2), "userAccountControl": uac}


async def _check_okta(user_id: str, creds: dict) -> dict:
    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.get(f"/api/v1/users/{user_id}", headers=headers)
        resp.raise_for_status()
        status = resp.json().get("status")

    return {"is_disabled": status in ("DEPROVISIONED", "SUSPENDED"), "status": status}


async def _check_entra(object_id: str, creds: dict) -> dict:
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

        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{object_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"$select": "accountEnabled"},
        )
        resp.raise_for_status()
        account_enabled = resp.json().get("accountEnabled", True)

    return {"is_disabled": not account_enabled, "accountEnabled": account_enabled}


async def _check_google(primary_email: str, creds: dict) -> dict:
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
        user = service.users().get(userKey=primary_email).execute()
        return user.get("suspended", False)

    suspended = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {"is_disabled": suspended, "suspended": suspended}


async def _check_github(login: str, creds: dict) -> dict:
    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        resp = await client.get(f"/orgs/{org}/members/{login}", headers=headers)
        # 404 = no longer a member (removed successfully), 204 = still active
        is_removed = resp.status_code == 404

    return {"is_disabled": is_removed, "membership_status": "removed" if is_removed else "active"}


async def _check_slack(member_id: str, creds: dict) -> dict:
    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        resp = await client.get("/users.info", headers=headers, params={"user": member_id})
        resp.raise_for_status()
        deleted = resp.json().get("user", {}).get("deleted", False)

    return {"is_disabled": deleted, "deleted": deleted}


async def _check_crowdstrike(device_ids_str: str, creds: dict) -> dict:
    import httpx
    device_ids = [d for d in device_ids_str.split(",") if d]
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    base_url = creds.get("base_url", "https://api.crowdstrike.com")

    async with httpx.AsyncClient(base_url=base_url) as client:
        token_resp = await client.post("/oauth2/token", data={"client_id": client_id, "client_secret": client_secret})
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/devices/entities/devices/v2",
            headers=headers,
            params=[("ids", d) for d in device_ids],
        )
        resp.raise_for_status()
        devices = resp.json().get("resources", [])
        all_isolated = all(d.get("status") == "contained" for d in devices)

    return {"is_disabled": all_isolated, "device_count": len(devices), "all_isolated": all_isolated}


_CHECKERS = {
    "active_directory": _check_ad,
    "okta": _check_okta,
    "entra_id": _check_entra,
    "google_workspace": _check_google,
    "github": _check_github,
    "slack": _check_slack,
    "crowdstrike": _check_crowdstrike,
}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "verify_disabled_rollback", "skipped": True, "reason": "verification is read-only"}
