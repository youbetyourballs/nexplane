# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.gcp.rotate_service_account_key import execute as gcp_rotate
    result = await gcp_rotate(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.rotate_service_account_key import rollback as gcp_rb
    return await gcp_rb(parameters, execution_result, connector)
