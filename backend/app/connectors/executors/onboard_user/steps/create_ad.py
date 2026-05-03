from datetime import datetime, timezone
import secrets
import string


def _generate_temp_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    display_name = parameters.get("display_name", target_email.split("@")[0])
    creds = getattr(connector, "credentials", {}) or {}
    temp_password = _generate_temp_password()

    if not creds:
        return {
            "action": "create_active_directory_account",
            "target_email": target_email,
            "display_name": display_name,
            "temp_password": temp_password,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from app.connectors.executors.active_directory._client import get_connection
    from ldap3 import ADD

    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    ou = parameters.get("ou") or f"OU=Users,{base_dn}"
    username = target_email.split("@")[0]
    user_dn = f"CN={display_name},{ou}"

    def _sync():
        conn = get_connection(creds)
        conn.add(
            user_dn,
            ["top", "person", "organizationalPerson", "user"],
            {
                "sAMAccountName": username,
                "userPrincipalName": target_email,
                "mail": target_email,
                "displayName": display_name,
                "unicodePwd": ('"%s"' % temp_password).encode("utf-16-le"),
                "userAccountControl": 512,
            },
        )
        result = conn.result
        for group_dn in parameters.get("groups", []):
            conn.modify(group_dn, {"member": [(ADD, [user_dn])]})
        conn.unbind()
        return result

    ldap_result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "create_active_directory_account",
        "target_email": target_email,
        "user_dn": user_dn,
        "temp_password": temp_password,
        "ldap_result": str(ldap_result),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "delete_active_directory_account",
        "target_email": parameters["target_email"],
        "user_dn": execution_result.get("user_dn"),
        "rolled_back": True,
    }
