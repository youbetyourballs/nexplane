import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_iam_bindings", "bindings": [
            {"member": "serviceAccount:mock@project.iam.gserviceaccount.com", "role": "roles/editor", "condition": None}
        ], "count": 1}
    from ._client import get_credentials, get_project_id
    from google.cloud import resourcemanager_v3
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = resourcemanager_v3.ProjectsClient(credentials=credentials)
    policy = await loop.run_in_executor(None, lambda: client.get_iam_policy(resource=f"projects/{project}"))
    bindings = []
    for binding in policy.bindings:
        for member in binding.members:
            bindings.append({
                "member": member,
                "role": binding.role,
                "condition": binding.condition.expression if binding.condition else None,
            })
    return {"action": "discover_iam_bindings", "bindings": bindings, "count": len(bindings)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
