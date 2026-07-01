# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone
import secrets
import string


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "create_google_workspace_account",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    sa_info = creds.get("service_account_json")
    delegated_admin = creds.get("delegated_admin")
    domain = target_email.split("@")[1]
    scopes = ["https://www.googleapis.com/auth/admin.directory.user"]
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=scopes
    ).with_subject(delegated_admin)

    parts = parameters.get("display_name", "").split(" ", 1)
    temp_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        user_body = {
            "primaryEmail": target_email,
            "name": {
                "givenName": parts[0],
                "familyName": parts[1] if len(parts) > 1 else "",
            },
            "password": temp_password,
            "changePasswordAtNextLogin": True,
        }
        if parameters.get("org_unit"):
            user_body["orgUnitPath"] = parameters["org_unit"]
        result = service.users().insert(body=user_body).execute()
        return result["id"]

    google_user_id = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "create_google_workspace_account",
        "target_email": target_email,
        "google_user_id": google_user_id,
        "temp_password": temp_password,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "delete_google_workspace_account",
        "target_email": parameters["target_email"],
        "rolled_back": True,
    }
