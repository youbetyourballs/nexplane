import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    org = parameters.get("org") or creds.get("org", "")
    role = parameters.get("role", "member")

    if not creds:
        return {
            "action": "add_org_member",
            "username": username,
            "org": org,
            "role": role,
            "added": True,
            "simulated": True,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        organization = g.get_organization(org)
        user = g.get_user(username)
        organization.add_to_members(user, role=role)
        membership = organization.get_membership(user)
        return membership.state, membership.role

    state, actual_role = await loop.run_in_executor(None, _call)
    return {
        "action": "add_org_member",
        "username": username,
        "org": org,
        "role": actual_role,
        "state": state,
        "added": True,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = execution_result.get("username") or parameters.get("username")
    org = execution_result.get("org") or parameters.get("org") or creds.get("org", "")

    if not creds:
        return {"rolled_back": True, "username": username, "org": org, "simulated": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        organization = g.get_organization(org)
        user = g.get_user(username)
        organization.remove_from_members(user)

    await loop.run_in_executor(None, _call)
    return {"rolled_back": True, "username": username, "org": org}
