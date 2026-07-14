# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

from ._client import get_artifacts_client

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "Container image layer data is permanently deleted and cannot be recovered"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    image_id = parameters.get("image_id", "")

    if not creds:
        return {"action": "delete_ocir_image", "image_id": image_id, "mock": True}

    client = get_artifacts_client(creds)
    loop = asyncio.get_running_loop()

    await loop.run_in_executor(None, lambda: client.delete_container_image(image_id=image_id))
    return {
        "action": "delete_ocir_image",
        "image_id": image_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": ROLLBACK_REASON}
