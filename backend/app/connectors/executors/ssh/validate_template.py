# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone
from app.services.safety_engine import APPROVED_COMMAND_TEMPLATES

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    template_id = parameters.get("template_id")
    if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
        raise ValueError(f"Command template '{template_id}' is not approved. Allowed: {list(APPROVED_COMMAND_TEMPLATES.keys())}")
    return {"action": "validate_template", "template_id": template_id, "valid": True, "validated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation has no rollback"}
