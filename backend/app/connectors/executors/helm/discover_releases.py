import asyncio
import base64
import gzip
import json

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_releases", "releases": [{"name": "my-app", "namespace": "default", "chart": "nginx-1.0.0", "status": "deployed"}], "count": 1}
    from ._client import get_k8s_core_client
    loop = asyncio.get_event_loop()
    core = get_k8s_core_client(creds)
    ns = creds.get("namespace")
    if ns:
        secrets = await loop.run_in_executor(None, lambda: core.list_namespaced_secret(ns, label_selector="owner=helm,status=deployed"))
    else:
        secrets = await loop.run_in_executor(None, lambda: core.list_secret_for_all_namespaces(label_selector="owner=helm,status=deployed"))
    releases = []
    for s in secrets.items:
        labels = s.metadata.labels or {}
        releases.append({"name": labels.get("name"), "namespace": s.metadata.namespace, "chart": labels.get("chart"), "version": labels.get("version"), "status": labels.get("status")})
    return {"action": "discover_releases", "releases": releases, "count": len(releases)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
