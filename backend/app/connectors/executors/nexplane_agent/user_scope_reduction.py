# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.aws.scope_reduction_iam import execute as iam_reduce
    result = await iam_reduce(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.scope_reduction_iam import rollback as iam_restore
    return await iam_restore(parameters, execution_result, connector)
