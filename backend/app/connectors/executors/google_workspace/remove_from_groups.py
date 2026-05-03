import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "remove_from_groups",
            "user_email": user_email,
            "groups_removed": [],
            "mock": True,
        }

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        groups_resp = service.groups().list(userKey=user_email).execute()
        groups = groups_resp.get("groups", [])
        removed = []
        errors = []
        for group in groups:
            group_email = group["email"]
            if dry_run:
                removed.append(group_email)
                continue
            try:
                service.members().delete(groupKey=group_email, memberKey=user_email).execute()
                removed.append(group_email)
            except Exception as exc:
                errors.append({"group": group_email, "error": str(exc)})
        return {"groups_removed": removed, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "remove_from_groups",
        "user_email": user_email,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Re-add user to each group they were removed from."""
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    groups = execution_result.get("groups_removed", [])

    if not creds:
        return {"action": "restore_group_memberships", "user_email": user_email, "groups_restored": groups, "mock": True}

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _restore():
        restored = []
        errors = []
        for group_email in groups:
            try:
                service.members().insert(
                    groupKey=group_email,
                    body={"email": user_email, "role": "MEMBER"},
                ).execute()
                restored.append(group_email)
            except Exception as exc:
                errors.append({"group": group_email, "error": str(exc)})
        return {"groups_restored": restored, "errors": errors}

    result = await loop.run_in_executor(None, _restore)
    return {"action": "restore_group_memberships", "user_email": user_email, **result}
