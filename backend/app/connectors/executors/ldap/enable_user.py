from ._client import get_ldap_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    if not username:
        raise ValueError("username is required")
    client = get_ldap_client(connector)
    if not client:
        return {"action": "ldap_enable_user", "status": "skipped",
                "reason": "no_ldap_credentials", "username": username}
    result = client.enable_user(username)
    return {"action": "ldap_enable_user", "username": username, **result}
