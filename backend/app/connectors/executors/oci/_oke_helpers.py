# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def poll_work_request(client, work_request_id: str, entity_type: str, timeout: int) -> str:
    """Poll an OKE work request until SUCCEEDED. Returns the matching resource identifier."""
    loop = asyncio.get_running_loop()
    deadline = time.time() + timeout
    while time.time() < deadline:
        wr = await loop.run_in_executor(
            None, lambda: client.get_work_request(work_request_id).data
        )
        if wr.status == "SUCCEEDED":
            for r in wr.resources:
                if r.entity_type.lower() == entity_type.lower():
                    return r.identifier
            raise RuntimeError(
                f"Work request {work_request_id} SUCCEEDED but no {entity_type} resource found"
            )
        if wr.status == "FAILED":
            raise RuntimeError(f"OKE work request {work_request_id} FAILED")
        await asyncio.sleep(15)
    raise TimeoutError(f"OKE work request {work_request_id} did not complete within {timeout}s")
