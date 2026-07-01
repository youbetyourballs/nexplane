# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from email.message import EmailMessage


WELCOME_SUBJECT = "Welcome to {org_name} — Your account is ready"

WELCOME_BODY = """Hi {recipient_name},

Your account has been created. Here are your login details:

  Login URL:        {login_url}
  Temporary Password: {temp_password}

You will be prompted to change your password on first login.

If you have any questions, contact your IT team.

— IT Operations
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    to_address = parameters["to_address"]
    recipient_name = parameters.get("recipient_name", "")
    temp_password = parameters.get("temp_password", "")
    login_url = parameters.get("login_url", "")

    if not creds:
        return {
            "action": "send_welcome_email",
            "to_address": to_address,
            "simulated": True,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

    return await _real_send(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """The email itself cannot be unsent. Remove the account that was created upstream."""
    creds = getattr(connector, "credentials", {})
    account_dn = execution_result.get("account_dn")
    okta_user_id = execution_result.get("okta_user_id")

    account_removed = False
    okta_deactivated = False

    if account_dn and creds.get("ad_creds"):
        try:
            from app.connectors.executors.active_directory._client import get_connection
            def _delete():
                conn = get_connection(creds["ad_creds"])
                conn.delete(account_dn)
                conn.unbind()
            await asyncio.get_event_loop().run_in_executor(None, _delete)
            account_removed = True
        except Exception:
            pass

    if okta_user_id and creds.get("okta_creds"):
        try:
            import httpx
            from app.connectors.executors.okta._client import okta_headers, okta_base
            okta_creds = creds["okta_creds"]
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{okta_base(okta_creds)}/users/{okta_user_id}/lifecycle/deactivate",
                    headers=okta_headers(okta_creds),
                )
            okta_deactivated = True
        except Exception:
            pass

    return {
        "rolled_back": True,
        "note": "email cannot be unsent; upstream account removal attempted",
        "account_removed": account_removed,
        "okta_deactivated": okta_deactivated,
    }


async def _real_send(parameters: dict, creds: dict) -> dict:
    from ._client import get_smtp_connection

    to_address = parameters["to_address"]
    recipient_name = parameters.get("recipient_name", "")
    temp_password = parameters.get("temp_password", "")
    login_url = parameters.get("login_url", "")
    org_name = parameters.get("org_name", "your organization")
    from_address = creds.get("from_address", creds.get("username", "noreply@localhost"))

    subject = WELCOME_SUBJECT.format(org_name=org_name)
    body = WELCOME_BODY.format(
        recipient_name=recipient_name,
        login_url=login_url,
        temp_password=temp_password,
    )

    def _sync():
        msg = EmailMessage()
        msg["From"] = from_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.set_content(body)
        conn = get_smtp_connection(creds)
        conn.send_message(msg)
        conn.quit()

    await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "send_welcome_email",
        "to_address": to_address,
        "subject": subject,
        "from_address": from_address,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
