# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    project = parameters["project"]
    stack = parameters["stack"]
    if not creds:
        return {"action": "refresh_stack", "project": project, "stack": stack, "refreshed": True}
    # Pulumi Cloud API does not expose a direct refresh endpoint
    # Refresh is a CLI operation — return guidance
    return {"action": "refresh_stack", "project": project, "stack": stack, "info": "Run 'pulumi refresh' via CLI for stack refresh", "refreshed": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "refresh has no rollback"}
