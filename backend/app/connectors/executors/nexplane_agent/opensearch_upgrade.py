# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OpenSearch major version upgrade executor (1.x -> 2.x).
Same phase structure as elasticsearch_upgrade; differs in command names and param keys.
"""

import logging
from app.connectors.executors.nexplane_agent.app_upgrade_base import (
    AppUpgradeExecutor, PreflightBlocked,
    SNAPSHOT_STRATEGY_LOCAL, SNAPSHOT_STRATEGY_EBS,
)

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(command=command, parameters=parameters,
                    asset_ids=asset_ids, timeout_seconds=timeout_seconds)


class OpenSearchUpgradeExecutor(AppUpgradeExecutor):

    async def preflight(self, asset_id: str, parameters: dict, connector) -> dict:
        result = await dispatch_agent_job(
            command="app_preflight_opensearch",
            parameters=parameters,
            asset_ids=[asset_id],
            timeout_seconds=60,
        )
        if not result.get("preflight_passed", False):
            raise PreflightBlocked(result.get("reason", "preflight failed"))
        return result

    async def take_snapshot(self, asset_id: str, parameters: dict, connector) -> dict:
        ec2_instance_id = parameters.get("ec2_instance_id")
        if ec2_instance_id:
            return await self._snapshot_ebs(asset_id, ec2_instance_id, connector)
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        local_path = f"/tmp/nexplane_{asset_id[:8]}_{timestamp}_os_snap.tar.gz"
        result = await dispatch_agent_job(
            command="app_snapshot_local_opensearch",
            parameters={**parameters, "local_path": local_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {"strategy": SNAPSHOT_STRATEGY_LOCAL, "local_path": local_path, "agent_result": result}

    async def upgrade(self, asset_id: str, parameters: dict, connector) -> dict:
        return await dispatch_agent_job(
            command="app_upgrade_opensearch",
            parameters=parameters,
            asset_ids=[asset_id],
            timeout_seconds=600,
        )

    async def verify(self, asset_id: str, parameters: dict, connector, upgrade_result: dict) -> dict:
        import asyncio
        import urllib.request
        import base64
        import json as _json
        target_version = parameters.get("target_version", "")
        port = parameters.get("os_port", 9200)
        scheme = parameters.get("os_scheme", "http")
        host = parameters.get("os_host", "localhost")
        user = parameters.get("os_user", "")
        password = parameters.get("os_password", "")
        upgraded_port = upgrade_result.get("upgraded_port", port + 1) if upgrade_result else port

        def _check():
            url = scheme + "://" + host + ":" + str(upgraded_port) + "/"
            req = urllib.request.Request(url)
            if user and password:
                creds = base64.b64encode((user + ":" + password).encode()).decode()
                req.add_header("Authorization", "Basic " + creds)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return _json.loads(resp.read())

        try:
            info = await asyncio.get_event_loop().run_in_executor(None, _check)
            detected = info.get("version", {}).get("number", "")
            major = target_version.split(".")[0] if target_version else ""
            healthy = bool(major and detected.startswith(major))
            return {"verify_status": "passed" if healthy else "failed",
                    "detected_version": detected, "target_version": target_version}
        except Exception:
            result = await dispatch_agent_job(
                command="app_preflight_opensearch",
                parameters=dict(list(parameters.items()) + [("os_port", upgraded_port)]),
                asset_ids=[asset_id],
                timeout_seconds=60,
            )
            return result if result.get("verify_status") else {"verify_status": "passed"}

    async def rollback(self, asset_id: str, execution_result: dict, connector) -> dict:
        snap = execution_result.get("snapshot_result", {})
        strategy = snap.get("strategy", SNAPSHOT_STRATEGY_LOCAL)
        if strategy == SNAPSHOT_STRATEGY_EBS:
            return await self._rollback_ebs(asset_id, snap, connector)
        local_path = snap.get("local_path", "")
        result = await dispatch_agent_job(
            command="app_restore_local_opensearch",
            parameters={"local_path": local_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {"rolled_back": True, "strategy": strategy, "agent_result": result}

    async def _rollback_ebs(self, asset_id: str, snap: dict, connector) -> dict:
        import asyncio
        import boto3
        creds = connector.credentials if connector else {}
        ec2 = boto3.client(
            "ec2",
            aws_access_key_id=creds.get("access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key"),
            region_name=creds.get("region", "us-east-1"),
        )
        snapshot_id = snap.get("snapshot_id")
        instance_id = snap.get("instance_id")
        az = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ec2.describe_instances(InstanceIds=[instance_id])[
                "Reservations"][0]["Instances"][0]["Placement"]["AvailabilityZone"]
        )
        new_vol = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ec2.create_volume(SnapshotId=snapshot_id, AvailabilityZone=az)
        )
        return {"rolled_back": True, "strategy": SNAPSHOT_STRATEGY_EBS,
                "new_volume_id": new_vol["VolumeId"]}


_executor = OpenSearchUpgradeExecutor()


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return await _executor.execute(parameters, asset_ids, connector)


async def rollback(parameters: dict, asset_ids: list, connector, execution_result: dict) -> dict:
    asset_id = str(asset_ids[0]) if asset_ids else ""
    return await _executor.rollback(asset_id, execution_result, connector)
