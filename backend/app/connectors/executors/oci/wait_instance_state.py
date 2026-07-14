# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Wait action has no side effects to roll back; it only polls state"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Poll compute.get_instance() every 10s up to 10 minutes until target_state is reached."""
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    target_state = parameters.get("target_state", "RUNNING")

    if not creds:
        return {
            "action": "wait_instance_state",
            "instance_id": instance_id,
            "target_state": target_state,
            "reached": True,
            "mock": True,
        }

    from ._client import get_compute_client

    compute = get_compute_client(creds)
    loop = asyncio.get_running_loop()

    max_polls = 120  # 120 x 10s = 20 minutes (OCI SOFTRESET cycles STOPPING->STOPPED->STARTING->RUNNING)
    for attempt in range(max_polls):
        instance = await loop.run_in_executor(
            None,
            lambda: compute.get_instance(instance_id).data,
        )
        current_state = instance.lifecycle_state
        if current_state == target_state:
            return {
                "action": "wait_instance_state",
                "instance_id": instance_id,
                "target_state": target_state,
                "current_state": current_state,
                "reached": True,
                "polls": attempt + 1,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        if current_state == "TERMINATED" and target_state != "TERMINATED":
            raise RuntimeError(
                f"Instance {instance_id} reached TERMINATED while waiting for {target_state}."
            )
        await asyncio.sleep(10)

    raise TimeoutError(
        f"Instance {instance_id} did not reach state '{target_state}' within 10 minutes."
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait has no rollback"}
