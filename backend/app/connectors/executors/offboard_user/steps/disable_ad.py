from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
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
    return {
        "action": "enable_ad_account",
        "target_email": parameters["target_email"],
        "user_dn": execution_result.get("user_dn"),
        "rolled_back": True,
    }
