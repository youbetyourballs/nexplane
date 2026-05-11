import asyncio
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    client = get_identity_client(creds)
    tenancy_id = creds.get("tenancy_id") or creds.get("tenancy", "")
    loop = asyncio.get_running_loop()
    import oci.pagination
    groups = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            client.list_groups, compartment_id=tenancy_id
        ).data,
    )
    assets = []
    for g in groups:
        if g.lifecycle_state == "DELETED":
            continue
        assets.append({
            "name": g.name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "group_id": g.id,
                "name": g.name,
                "description": g.description or "",
                "lifecycle_state": g.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-iam-group"],
            "_dedup_key": g.id,
        })
    return {"assets": assets, "count": len(assets)}
