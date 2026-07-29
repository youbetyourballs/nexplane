# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Disable the AD account for target_email. Uses userAccountControl=514 (disabled)."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "disable_ad_account",
            "target_email": target_email,
            "disabled": True,
            "simulated": True,
            "disabled_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from app.connectors.executors.active_directory._client import get_connection
    from ldap3 import MODIFY_REPLACE

    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(base_dn, f"(mail={target_email})", attributes=["distinguishedName"])
        if not conn.entries:
            conn.search(base_dn, f"(userPrincipalName={target_email})", attributes=["distinguishedName"])
        if not conn.entries:
            conn.unbind()
            raise ValueError(f"User {target_email} not found in AD")
        user_dn = conn.entries[0].distinguishedName.value
        conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        result = conn.result
        conn.unbind()
        return user_dn, result

    user_dn, ldap_result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "disable_ad_account",
        "target_email": target_email,
        "user_dn": user_dn,
        "disabled": True,
        "ldap_result": str(ldap_result),
        "disabled_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Re-enable the AD account (userAccountControl=512)."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "enable_ad_account",
            "target_email": target_email,
            "rolled_back": True,
            "simulated": True,
        }

    import asyncio
    from app.connectors.executors.active_directory._client import get_connection
    from ldap3 import MODIFY_REPLACE

    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    user_dn = execution_result.get("user_dn")

    def _sync():
        conn = get_connection(creds)
        if not user_dn:
            conn.search(base_dn, f"(mail={target_email})", attributes=["distinguishedName"])
            if not conn.entries:
                conn.search(base_dn, f"(userPrincipalName={target_email})", attributes=["distinguishedName"])
            if not conn.entries:
                conn.unbind()
                raise ValueError(f"User {target_email} not found in AD for rollback")
            dn = conn.entries[0].distinguishedName.value
        else:
            dn = user_dn
        # Clear "must change password" flag before enabling — AD won't enable
        # an account with pwdLastSet=0 (error 53 unwillingToPerform).
        # Setting pwdLastSet=-1 marks the password as freshly set (no expiry).
        conn.modify(dn, {"pwdLastSet": [(MODIFY_REPLACE, [-1])]})
        conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [512])]})
        result = conn.result
        conn.unbind()
        return dn, result

    dn, ldap_result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "enable_ad_account",
        "target_email": target_email,
        "user_dn": dn,
        "rolled_back": True,
        "ldap_result": str(ldap_result),
    }
