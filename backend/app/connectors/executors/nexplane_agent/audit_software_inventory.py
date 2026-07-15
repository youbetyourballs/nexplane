# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"packages": [{"name": "openssl", "version": "3.0.2", "source": "dpkg"}, {"name": "nginx", "version": "1.24.0", "source": "dpkg"}], "total": 2, "platform": "linux", "audited_at": datetime.now(timezone.utc).isoformat()}
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_software_inventory is read-only"}
