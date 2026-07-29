# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging

logger = logging.getLogger(__name__)

DEFINITION = {
    "name": "linux_parallel_upgrade",
    "display_name": "Linux Parallel Upgrade",
    "rollback_supported": True,
    "rollback_capability": "full",
}

ROLLBACK_CAPABILITY_FULL = "full"
ROLLBACK_CAPABILITY_IRREVERSIBLE = "irreversible"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Phase 1-6: preflight → snapshot → sync → health check → cutover → decommission scheduling."""
    raise NotImplementedError("linux_parallel_upgrade execute — not yet implemented")


async def rollback(parameters: dict, execution_result: dict, asset_ids: list, connector) -> dict:
    """Reverse cutover, restart source, cancel decommission job."""
    raise NotImplementedError("linux_parallel_upgrade rollback — not yet implemented")
