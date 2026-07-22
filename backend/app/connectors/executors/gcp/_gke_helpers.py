# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def poll_gke_operation(client, op_name: str, timeout: int, project_id: str = "", location: str = "") -> None:
    """Poll a GKE operation until DONE. Raises on failure or timeout.

    op_name may be a short name (operation-xxx) or a full resource path.
    If short, project_id and location are required to build the full path.
    """
    import google.cloud.container_v1 as container_v1
    loop = asyncio.get_event_loop()

    # Normalise to full resource path
    if not op_name.startswith("projects/"):
        if not project_id or not location:
            raise ValueError("project_id and location required when op_name is not a full resource path")
        op_name = f"projects/{project_id}/locations/{location}/operations/{op_name}"

    deadline = time.time() + timeout
    while True:
        op = await loop.run_in_executor(None, lambda: client.get_operation({"name": op_name}))
        if op.status == container_v1.Operation.Status.DONE:
            if op.status_message:
                raise RuntimeError(f"GKE operation failed: {op.status_message}")
            return
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError(f"GKE operation {op_name} did not complete within {timeout}s")
        await asyncio.sleep(min(15, remaining))
