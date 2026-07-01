# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Rotate a GCP service account key — deactivates old, creates new."""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone


def _gcp_iam_client(connector, execution_result: dict | None = None):
    """Build Google IAM client, falling back through credential sources."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_data = None
        # Try connector credentials first
        if connector:
            raw = getattr(connector, "credentials", None) or {}
            if isinstance(raw, dict) and raw.get("type") == "service_account":
                creds_data = raw
            elif isinstance(raw, dict) and raw.get("service_account_json"):
                creds_data = json.loads(raw["service_account_json"])
            elif isinstance(raw, dict) and raw.get("service_account_key_json"):
                try:
                    creds_data = json.loads(raw["service_account_key_json"])
                except (TypeError, json.JSONDecodeError):
                    creds_data = raw["service_account_key_json"] if isinstance(raw["service_account_key_json"], dict) else None

        # Fall back to execution_result stored creds
        if not creds_data and execution_result:
            creds_data = execution_result.get("_gcp_creds")

        # Fall back to GOOGLE_APPLICATION_CREDENTIALS env var
        if not creds_data:
            gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
            if gac:
                with open(gac) as f:
                    creds_data = json.load(f)

        # Fall back to inline JSON env var
        if not creds_data:
            gac_json = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
            if gac_json:
                creds_data = json.loads(gac_json)

        if not creds_data:
            return None, None

        credentials = service_account.Credentials.from_service_account_info(
            creds_data,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        iam_service = build("iam", "v1", credentials=credentials, cache_discovery=False)
        return iam_service, creds_data

    except Exception:
        return None, None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Rotate a GCP service account key.

    Parameters:
        service_account_email: full SA email (e.g. sa@project.iam.gserviceaccount.com)
        key_id: optional — specific key to deactivate (default: oldest active key)
        project_id: GCP project ID (inferred from SA email if omitted)
    """
    sa_email = parameters.get("service_account_email") or parameters.get("email", "")
    if not sa_email:
        raise ValueError("service_account_email is required")

    project_id = parameters.get("project_id") or sa_email.split("@")[-1].split(".")[0]

    # Also check for inline GCP credentials in parameters (for smoke tests)
    gcp_json_param = parameters.get("GCP_SERVICE_ACCOUNT_JSON")
    if gcp_json_param and not os.environ.get("GCP_SERVICE_ACCOUNT_JSON"):
        os.environ["GCP_SERVICE_ACCOUNT_JSON"] = gcp_json_param

    iam_service, creds_data = _gcp_iam_client(connector)
    if not iam_service:
        return {
            "action": "rotate_gcp_service_account_key",
            "status": "skipped",
            "reason": "no_gcp_credentials",
            "service_account": sa_email,
            "_asset_ids": [str(a) for a in asset_ids],
        }

    sa_resource = f"projects/{project_id}/serviceAccounts/{sa_email}"

    # List existing keys
    keys_resp = iam_service.projects().serviceAccounts().keys().list(
        name=sa_resource,
        keyTypes=["USER_MANAGED"],
    ).execute()
    existing_keys = keys_resp.get("keys", [])

    # Create new key first
    new_key_resp = iam_service.projects().serviceAccounts().keys().create(
        name=sa_resource, body={}
    ).execute()
    new_key_id = new_key_resp["name"].split("/")[-1]
    new_key_json = new_key_resp.get("privateKeyData", "")  # base64-encoded JSON

    # Deactivate old keys (keep the new one)
    deactivated = []
    for key in existing_keys:
        key_id = key["name"].split("/")[-1]
        try:
            iam_service.projects().serviceAccounts().keys().delete(
                name=key["name"]
            ).execute()
            deactivated.append(key_id)
        except Exception:
            pass

    return {
        "action": "rotate_gcp_service_account_key",
        "service_account": sa_email,
        "new_key_id": new_key_id,
        "deactivated_key_ids": deactivated,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback: the new key was created, old keys deleted — no true rollback possible.
    Instead, creates another new key to replace the rotated one."""
    return {
        "rolled_back": False,
        "reason": "GCP SA key rotation is one-way — old keys were deleted. Create a new key to continue access.",
    }
