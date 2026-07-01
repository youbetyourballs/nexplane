# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: audit_patch_status — runs audit_patch_status agent command."""
from __future__ import annotations


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    if not asset_ids:
        raise ValueError("asset_ids required for audit_patch_status")
    return await dispatch_agent_job(
        command="audit_patch_status",
        parameters=parameters,
        asset_ids=[str(asset_ids[0])],
        timeout_seconds=120,
    )


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "audit is read-only"}
