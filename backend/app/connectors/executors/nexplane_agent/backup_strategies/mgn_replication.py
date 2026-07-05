# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Backup strategy: AWS MGN launch_test_instances -> capture launched AMI. Machine tier."""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _mgn_client(creds: dict):
    import boto3
    return boto3.client(
        "mgn",
        region_name=creds.get("region", creds.get("aws_region", "us-east-1")),
        aws_access_key_id=creds.get("access_key_id", creds.get("aws_access_key_id")),
        aws_secret_access_key=creds.get("secret_access_key", creds.get("aws_secret_access_key")),
        aws_session_token=creds.get("session_token", creds.get("aws_session_token")),
    )


async def backup(params: dict, asset_ids: list, connector) -> dict:
    from app.connectors.executors.nexplane_agent.aws_utils import _load_aws_creds, _ec2_client

    aws_connector_id = params.get("aws_connector_id", "")
    source_server_id = params.get("mgn_source_server_id", "")
    if not source_server_id:
        raise RuntimeError("mgn_replication: mgn_source_server_id is required")

    creds = await _load_aws_creds(aws_connector_id, connector)
    captured_at = datetime.now(timezone.utc).isoformat()

    def _sync_launch():
        mgn = _mgn_client(creds)
        ec2 = _ec2_client(creds)

        # Verify source server is ready
        desc = mgn.describe_source_servers(
            filters={"sourceServerIDs": [source_server_id]}
        ).get("items", [])
        if not desc:
            raise RuntimeError(f"mgn_replication: source server {source_server_id} not found")
        state = desc[0].get("lifeCycle", {}).get("state", "")
        if state not in ("READY_FOR_TEST", "READY_FOR_CUTOVER", "TESTING", "CUTTING_OVER"):
            raise RuntimeError(
                f"mgn_replication: source {source_server_id} not ready (state={state})"
            )

        job = mgn.launch_test_instances(sourceServerIDs=[source_server_id])["job"]
        job_id = job["jobID"]

        deadline = time.time() + 1800
        launched_instance_id = ""
        while time.time() < deadline:
            jobs = mgn.describe_jobs(filters={"jobIDs": [job_id]}).get("items", [])
            if jobs:
                j = jobs[0]
                jstatus = j.get("status", "")
                if jstatus == "COMPLETED":
                    for ps in j.get("participatingServers", []):
                        if ps.get("sourceServerID") == source_server_id:
                            launched_instance_id = ps.get("launchedEc2InstanceID", "")
                    break
                if jstatus == "FAILED":
                    raise RuntimeError(f"mgn_replication: launch job {job_id} FAILED")
            time.sleep(30)
        if not launched_instance_id:
            raise TimeoutError(f"mgn_replication: job {job_id} did not produce a test instance")

        inst = ec2.describe_instances(
            InstanceIds=[launched_instance_id]
        )["Reservations"][0]["Instances"][0]
        ami_id = inst.get("ImageId", "")
        return job_id, launched_instance_id, ami_id

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        job_id, test_instance_id, ami_id = await loop.run_in_executor(pool, _sync_launch)

    artifact_refs = {
        "capture_strategy": "mgn_replication",
        "restore_strategy": "launch_ami",
        "backup_tier": "machine",
        "captured_at": captured_at,
        "mgn_source_server_id": source_server_id,
        "launch_job_id": job_id,
        "test_instance_id": test_instance_id,
        "ami_id": ami_id,
        "aws_connector_id": aws_connector_id,
    }
    return {
        "status": "completed",
        "artifact_refs": artifact_refs,
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(params: dict, execution_result: dict, connector) -> dict:
    """No-op: the MGN source server stays registered/replicating for the next
    backup. Terminating the test instance and deregistering its AMI is the
    smoke phase's teardown responsibility, not a rollback of the backup itself."""
    refs = execution_result.get("artifact_refs", {})
    return {
        "rolled_back": True,
        "noop": True,
        "reason": "mgn source retained for next backup",
        "mgn_source_server_id": refs.get("mgn_source_server_id", ""),
    }
