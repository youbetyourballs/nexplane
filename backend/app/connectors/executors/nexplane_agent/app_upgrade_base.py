# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Shared base class for application version upgrade executors.

Subclasses override: preflight(), upgrade(), verify(), rollback().
The base class runs the phase scaffold and handles snapshot tiering.
"""

import logging

logger = logging.getLogger(__name__)

SNAPSHOT_STRATEGY_EBS   = "ebs"
SNAPSHOT_STRATEGY_S3    = "s3"
SNAPSHOT_STRATEGY_LOCAL = "local"

ROLLBACK_CAPABILITY = "full"


class PreflightBlocked(Exception):
    """Raised by preflight() to signal a critical finding that prevents upgrade."""


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


class AppUpgradeExecutor:
    """Abstract base for application upgrade executors.

    Subclasses must implement: preflight, upgrade, verify, rollback.
    The execute() method runs the full phase scaffold.
    """

    async def execute(self, parameters: dict, asset_ids: list, connector) -> dict:
        if not asset_ids:
            raise ValueError("asset_ids required")

        asset_id = str(asset_ids[0])
        dry_run = bool(parameters.get("dry_run", False))
        skip_snapshot = bool(parameters.get("skip_snapshot", False))

        # Phase 1: Preflight
        try:
            preflight_result = await self.preflight(asset_id, parameters, connector)
        except PreflightBlocked as exc:
            return {"status": "preflight_blocked", "reason": str(exc)}

        if dry_run:
            return preflight_result

        # Phase 2: Snapshot
        if skip_snapshot:
            logger.warning("skip_snapshot=True for asset %s — no rollback artifact", asset_id)
            snapshot_result = {"strategy": "skipped", "snapshot_id": None}
        else:
            snapshot_result = await self.take_snapshot(asset_id, parameters, connector)

        # Phase 3: Upgrade
        try:
            upgrade_result = await self.upgrade(asset_id, parameters, connector)
        except Exception as exc:
            logger.error("Upgrade failed for asset %s: %s", asset_id, exc)
            return {
                "status": "upgrade_failed",
                "error": str(exc),
                "snapshot_result": snapshot_result,
                "upgrade_result": None,
            }

        # Phase 4: Verify
        verify_result = await self.verify(asset_id, parameters, connector, upgrade_result=upgrade_result)
        verify_status = verify_result.get("verify_status", "failed")

        return {
            "status": "completed" if verify_status == "passed" else "verify_failed",
            "preflight_result": preflight_result,
            "snapshot_result": snapshot_result,
            "upgrade_result": upgrade_result,
            "verify_result": verify_result,
        }

    async def take_snapshot(self, asset_id: str, parameters: dict, connector) -> dict:
        """Tiered snapshot: EBS -> S3 -> local. Subclasses may override for engine-specific dumps."""
        ec2_instance_id = parameters.get("ec2_instance_id")
        s3_bucket = parameters.get("snapshot_s3_bucket")

        if ec2_instance_id:
            return await self._snapshot_ebs(asset_id, ec2_instance_id, connector)
        elif s3_bucket:
            return await self._snapshot_s3(asset_id, s3_bucket, parameters, connector)
        else:
            return await self._snapshot_local(asset_id, parameters, connector)

    async def _snapshot_ebs(self, asset_id: str, ec2_instance_id: str, connector) -> dict:
        import asyncio
        creds = connector.credentials if connector else {}
        import boto3
        ec2 = boto3.client(
            "ec2",
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            region_name=creds.get("region", "us-east-1"),
        )
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ec2.describe_instances(InstanceIds=[ec2_instance_id])
        )
        instance = response["Reservations"][0]["Instances"][0]
        volume_id = instance["BlockDeviceMappings"][0]["Ebs"]["VolumeId"]

        snap = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ec2.create_snapshot(
                VolumeId=volume_id,
                Description=f"nexplane-app-upgrade-{asset_id[:8]}",
            )
        )
        return {
            "strategy": SNAPSHOT_STRATEGY_EBS,
            "snapshot_id": snap["SnapshotId"],
            "volume_id": volume_id,
            "instance_id": ec2_instance_id,
        }

    async def _snapshot_s3(self, asset_id: str, s3_bucket: str, parameters: dict, connector) -> dict:
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dump_path = f"nexplane-snapshots/{asset_id}/{timestamp}/app-snapshot.tar.gz"
        result = await dispatch_agent_job(
            command="app_snapshot_to_s3",
            parameters={"s3_bucket": s3_bucket, "s3_key": dump_path, **parameters},
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        return {
            "strategy": SNAPSHOT_STRATEGY_S3,
            "dump_path": dump_path,
            "s3_bucket": s3_bucket,
            "agent_result": result,
        }

    async def _snapshot_local(self, asset_id: str, parameters: dict, connector) -> dict:
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        local_path = f"/tmp/nexplane_{asset_id[:8]}_{timestamp}_app_snap.tar.gz"
        result = await dispatch_agent_job(
            command="app_snapshot_local",
            parameters={"local_path": local_path, **parameters},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {
            "strategy": SNAPSHOT_STRATEGY_LOCAL,
            "local_path": local_path,
            "agent_result": result,
        }

    async def preflight(self, asset_id: str, parameters: dict, connector) -> dict:
        raise NotImplementedError

    async def upgrade(self, asset_id: str, parameters: dict, connector) -> dict:
        raise NotImplementedError

    async def verify(self, asset_id: str, parameters: dict, connector, upgrade_result: dict) -> dict:
        raise NotImplementedError

    async def rollback(self, asset_id: str, execution_result: dict, connector) -> dict:
        raise NotImplementedError
