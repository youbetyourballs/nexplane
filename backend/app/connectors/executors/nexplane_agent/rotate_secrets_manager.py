# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Route to AWS Secrets Manager executor
    from app.connectors.executors.aws.rotate_secrets_manager_secret import execute as aws_rotate
    result = await aws_rotate(parameters, asset_ids, connector)
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.aws.rotate_secrets_manager_secret import rollback as aws_rb
    return await aws_rb(parameters, execution_result, connector)
