import asyncio
import time
from datetime import datetime, timezone

_POLL_INTERVAL_SECONDS = 10
_MAX_WAIT_SECONDS = 300


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    load_balancer_id = parameters.get("load_balancer_id", "")

    if not creds:
        return {
            "action": "delete_load_balancer",
            "load_balancer_id": load_balancer_id or "ocid1.loadbalancer.mock",
            "deleted": True,
            "mock": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_loadbalancer_client
    loop = asyncio.get_running_loop()
    lb_client = get_loadbalancer_client(creds)

    work_request_resp = await loop.run_in_executor(
        None, lambda: lb_client.delete_load_balancer(load_balancer_id)
    )
    work_request_id = work_request_resp.headers.get("opc-work-request-id")

    # Poll until SUCCEEDED
    deadline = time.monotonic() + _MAX_WAIT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        wr = await loop.run_in_executor(
            None, lambda: lb_client.get_work_request(work_request_id).data
        )
        if wr.lifecycle_state == "SUCCEEDED":
            break
        if wr.lifecycle_state in ("FAILED", "CANCELED"):
            raise RuntimeError(f"OCI delete_load_balancer work request {wr.lifecycle_state}")

    return {
        "action": "delete_load_balancer",
        "load_balancer_id": load_balancer_id,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
