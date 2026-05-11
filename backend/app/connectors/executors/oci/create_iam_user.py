import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("name", "nexplane-user")
    description = parameters.get("description", "Created by Nexplane")
    email = parameters.get("email", "")
    group_id = parameters.get("group_id", "")

    auto_asset = {
        "name": name,
        "asset_type": "identity",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {"name": name, "provider": "oci"},
        "tags": ["oci", "iam-user", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_iam_user",
            "name": name,
            "user_id": "ocid1.user.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    tenancy_id = creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        details = oci.identity.models.CreateUserDetails(
            compartment_id=tenancy_id,
            name=name,
            description=description,
            email=email or None,
        )
        user = client.create_user(details).data
        if group_id:
            membership = oci.identity.models.AddUserToGroupDetails(
                user_id=user.id, group_id=group_id
            )
            client.add_user_to_group(membership)
        return user

    user = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["user_id"] = user.id
    auto_asset["asset_metadata"]["lifecycle_state"] = user.lifecycle_state
    return {
        "action": "create_iam_user",
        "name": name,
        "user_id": user.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_iam_user import execute as delete
    return await delete(
        {"user_id": execution_result.get("user_id"), "name": execution_result.get("name")},
        [],
        connector,
    )
