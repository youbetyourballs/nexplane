# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _dispatch.dispatch_agent_job(
        command="audit_cis_compliance",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit_cis_compliance is read-only"}
