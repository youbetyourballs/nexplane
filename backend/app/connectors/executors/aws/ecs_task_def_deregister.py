# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""ECS task definition deregister executor.

Marks a task definition revision INACTIVE via deregister_task_definition.
Irreversible — ECS has no re-register API.
"""

import logging
from app.connectors.executors.aws.reference_scan import _boto_client

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "ECS has no API to re-register a deregistered task definition revision"


async def execute(cr, connector, db) -> dict:
    params = cr.parameters or {}
    task_def_arn = params["task_def_arn"]
    region = params.get("region")

    client = _boto_client("ecs", connector, region)
    client.deregister_task_definition(taskDefinition=task_def_arn)
    logger.info("Deregistered ECS task definition %s", task_def_arn)
    return {"deregistered": True, "task_def_arn": task_def_arn}
