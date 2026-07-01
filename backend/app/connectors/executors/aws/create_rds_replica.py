# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    replica_id = parameters["replica_db_instance_identifier"]
    source_id = parameters["source_db_instance_identifier"]
    instance_class = parameters.get("db_instance_class", "db.t3.micro")

    if not creds:
        return {
            "action": "create_rds_replica",
            "replica_db_instance_identifier": replica_id,
            "source_db_instance_identifier": source_id,
            "endpoint": "mock-replica.rds.amazonaws.com",
            "mock": True,
            "_auto_asset": {
                "name": replica_id,
                "asset_type": "database",
                "environment": "prod",
                "criticality": "high",
                "asset_metadata": {
                    "db_instance_identifier": replica_id,
                    "source_db_instance_identifier": source_id,
                    "provider": "aws",
                    "role": "replica",
                },
                "tags": ["rds", "replica", "nexplane-managed"],
            },
        }

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, "rds")
    loop = asyncio.get_event_loop()

    def _call():
        rds.create_db_instance_read_replica(
            DBInstanceIdentifier=replica_id,
            SourceDBInstanceIdentifier=source_id,
            DBInstanceClass=instance_class,
            PubliclyAccessible=False,
        )
        waiter = rds.get_waiter("db_instance_available")
        waiter.wait(
            DBInstanceIdentifier=replica_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )
        desc = rds.describe_db_instances(DBInstanceIdentifier=replica_id)
        inst = desc["DBInstances"][0]
        return inst.get("Endpoint", {}).get("Address", "")

    endpoint = await loop.run_in_executor(None, _call)
    return {
        "action": "create_rds_replica",
        "replica_db_instance_identifier": replica_id,
        "source_db_instance_identifier": source_id,
        "endpoint": endpoint,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": {
            "name": replica_id,
            "asset_type": "database",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "db_instance_identifier": replica_id,
                "source_db_instance_identifier": source_id,
                "provider": "aws",
                "role": "replica",
            },
            "tags": ["rds", "replica", "nexplane-managed"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Rollback = delete the replica instance."""
    creds = getattr(connector, "credentials", {})
    replica_id = parameters["replica_db_instance_identifier"]

    if not creds:
        return {"rolled_back": True, "mock": True}

    from ._client import get_boto3_client
    rds = get_boto3_client(creds, "rds")
    loop = asyncio.get_event_loop()

    def _delete():
        rds.delete_db_instance(
            DBInstanceIdentifier=replica_id,
            SkipFinalSnapshot=True,
        )
        waiter = rds.get_waiter("db_instance_deleted")
        waiter.wait(
            DBInstanceIdentifier=replica_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )

    await loop.run_in_executor(None, _delete)
    return {
        "rolled_back": True,
        "replica_db_instance_identifier": replica_id,
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
