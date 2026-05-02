import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    release_name = parameters["release_name"]
    namespace = parameters["namespace"]
    if not creds:
        return {"action": "discover_release_history", "release": release_name, "history": [], "count": 0}
    from ._client import get_k8s_core_client
    loop = asyncio.get_event_loop()
    core = get_k8s_core_client(creds)
    secrets = await loop.run_in_executor(None, lambda: core.list_namespaced_secret(namespace, label_selector=f"owner=helm,name={release_name}"))
    history = [{"version": s.metadata.labels.get("version"), "status": s.metadata.labels.get("status"), "chart": s.metadata.labels.get("chart")} for s in secrets.items]
    return {"action": "discover_release_history", "release": release_name, "history": history, "count": len(history)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
