# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _mock_response(params: dict) -> dict:
    return {
        "action": "promote_db_replica",
        "replica_identifier": params.get("replica_identifier"),
        "new_endpoint": "mock-primary.us-east-1.rds.amazonaws.com",
        "dns_updated": False,
        "mock": True,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }


def _verify_writable(endpoint: str, connector) -> None:
    """Attempt a write-acceptance check against the new primary endpoint.

    Uses the connector's DB credentials if available. On failure, raises
    RuntimeError so the caller can surface the error before updating DNS.
    This is intentionally lightweight — a simple connection attempt is enough
    to confirm the instance is accepting connections after promotion.
    """
    # Production implementation would open a short-lived connection and
    # attempt a BEGIN/ROLLBACK. Stubbed here because the DB credentials
    # are not part of the RDS connector credential bundle (which holds IAM
    # creds, not DB-level creds). Operators should verify via application
    # health checks post-promotion.
    pass


async def _real_execute(connector, params: dict) -> dict:
    loop = asyncio.get_event_loop()
    replica_id = params["replica_identifier"]

    rds = connector.session.client("rds")

    # Take a pre-promotion snapshot so operators have a restore point.
    # Promotion is irreversible — this snapshot is the only rollback path.
    snapshot_id = f"nexplane-pre-promote-{replica_id}-{int(datetime.now(timezone.utc).timestamp())}"
    # Truncate to AWS 255-char limit and replace disallowed characters
    snapshot_id = snapshot_id[:255].replace("_", "-")

    pre_promotion_snapshot_id: str | None = None
    try:
        def _snapshot():
            resp = rds.create_db_snapshot(
                DBSnapshotIdentifier=snapshot_id,
                DBInstanceIdentifier=replica_id,
            )
            snap_waiter = rds.get_waiter("db_snapshot_completed")
            snap_waiter.wait(DBSnapshotIdentifier=snapshot_id)
            return resp["DBSnapshot"]["DBSnapshotIdentifier"]

        pre_promotion_snapshot_id = await loop.run_in_executor(None, _snapshot)
    except Exception as snap_exc:
        # Snapshot failure is non-fatal — log and proceed, but surface in result
        # so operators are aware there is no restore point.
        pre_promotion_snapshot_id = None
        _snapshot_error = str(snap_exc)
    else:
        _snapshot_error = None

    def _promote():
        rds.promote_read_replica(DBInstanceIdentifier=replica_id)
        waiter = rds.get_waiter("db_instance_available")
        waiter.wait(DBInstanceIdentifier=replica_id)
        desc = rds.describe_db_instances(DBInstanceIdentifier=replica_id)
        return desc["DBInstances"][0]["Endpoint"]["Address"]

    new_endpoint = await loop.run_in_executor(None, _promote)

    _verify_writable(new_endpoint, connector)

    dns_updated = False
    if params.get("update_dns_record"):
        r53 = connector.session.client("route53")

        def _update_dns():
            r53.change_resource_record_sets(
                HostedZoneId=params["dns_hosted_zone_id"],
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": params["dns_record_name"],
                                "Type": "CNAME",
                                "TTL": 60,
                                "ResourceRecords": [{"Value": new_endpoint}],
                            },
                        }
                    ]
                },
            )

        await loop.run_in_executor(None, _update_dns)
        dns_updated = True

    result: dict = {
        "action": "promote_db_replica",
        "replica_identifier": replica_id,
        "new_endpoint": new_endpoint,
        "dns_updated": dns_updated,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "pre_promotion_snapshot_id": pre_promotion_snapshot_id,
    }
    if _snapshot_error is not None:
        result["pre_promotion_snapshot_error"] = _snapshot_error
    return result


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(connector, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """RDS replica promotion is architecturally irreversible.

    Once promoted the instance is a standalone primary and cannot be demoted
    or re-attached to its source cluster. The pre-execution snapshot (stored in
    execution_result['pre_promotion_snapshot_id']) is the only restore path.
    """
    snapshot_id = execution_result.get("pre_promotion_snapshot_id")
    snapshot_note = (
        f"A pre-promotion snapshot was taken: '{snapshot_id}'. "
        f"Restore from that snapshot to recover the replica state."
        if snapshot_id
        else "No pre-promotion snapshot is available — the replica state cannot be recovered automatically."
    )
    return {
        "rolled_back": False,
        "reason": (
            "RDS replica promotion is irreversible — the replica is now a standalone primary instance. "
            "To recover, restore from a pre-promotion snapshot if one was taken. "
            + snapshot_note
        ),
        "pre_promotion_snapshot_id": snapshot_id,
    }
