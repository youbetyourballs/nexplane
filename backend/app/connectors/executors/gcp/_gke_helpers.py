# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def poll_gke_operation(client, op_name: str, timeout: int) -> None:
    """Poll a GKE operation by full resource-path name until DONE. Raises on failure or timeout."""
    from google.cloud import container_v1
    loop = asyncio.get_event_loop()
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
