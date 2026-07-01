# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Rotate an Azure service principal client secret."""
from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta


def _graph_client(connector, execution_result=None):
    """Get Azure AD Graph API credentials."""
    creds = (connector.credentials if connector else None) or {}
    if not creds.get("tenant_id") and execution_result:
        creds = execution_result.get("_azure_creds") or creds
    if not all(k in creds for k in ("tenant_id", "client_id", "client_secret")):
        return None, None
    return creds, creds


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    app_id = parameters.get("app_id") or parameters.get("application_id", "")
    if not app_id:
        raise ValueError("app_id (Azure application/service principal ID) is required")

    creds, _ = _graph_client(connector)
    if not creds:
        return {
            "action": "rotate_azure_service_principal_secret",
            "status": "skipped",
            "reason": "no_azure_credentials",
            "app_id": app_id,
        }

    import httpx
    # Get token
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Add new secret (valid 90 days)
        new_secret_resp = await client.post(
            f"https://graph.microsoft.com/v1.0/applications/{app_id}/addPassword",
            headers=headers,
            json={
                "passwordCredential": {
                    "displayName": f"nexplane-rotated-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                    "endDateTime": (datetime.now(timezone.utc) + timedelta(days=90)).isoformat(),
                }
            },
        )
        new_secret_resp.raise_for_status()
        new_secret_data = new_secret_resp.json()
        new_key_id = new_secret_data["keyId"]
        new_secret_value = new_secret_data["secretText"]  # Only shown once

        # List and remove old secrets
        app_resp = await client.get(
            f"https://graph.microsoft.com/v1.0/applications/{app_id}",
            headers=headers,
        )
        app_resp.raise_for_status()
        old_creds = [c for c in app_resp.json().get("passwordCredentials", [])
                     if c["keyId"] != new_key_id]
        removed = []
        for cred in old_creds:
            try:
                await client.post(
                    f"https://graph.microsoft.com/v1.0/applications/{app_id}/removePassword",
                    headers=headers,
                    json={"keyId": cred["keyId"]},
                )
                removed.append(cred["keyId"])
            except Exception:
                pass

    return {
        "action": "rotate_azure_service_principal_secret",
        "app_id": app_id,
        "new_key_id": new_key_id,
        "new_secret_value": new_secret_value,  # Operator must update consumers
        "removed_key_ids": removed,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Azure SP secret rotation is one-way — update consumers with new_secret_value"}
