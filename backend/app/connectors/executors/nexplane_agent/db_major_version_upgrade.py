# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Database major version upgrade executor.
Flow: preflight -> snapshot -> upgrade -> verify -> (rollback on failure or operator request).
Engines: postgres (dump_restore or in_place), mysql (in_place), mongodb (sequential FCV bumps).
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

# Module-level import for patching in tests
try:
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
except ImportError:
    AsyncSessionLocal = None  # type: ignore
    Asset = None  # type: ignore

# Supported MongoDB major versions in sequential upgrade order
_MONGO_VERSION_SEQUENCE = ["4.4", "5.0", "6.0", "7.0"]

_ENGINE_DEFAULTS = {
    "postgres": {"port": 5432, "user": "postgres"},
    "mysql":    {"port": 3306, "user": "root"},
    "mongodb":  {"port": 27017, "user": "admin"},
}


def _default_port(engine: str) -> int:
    return _ENGINE_DEFAULTS[engine]["port"]


def _default_user(engine: str) -> str:
    return _ENGINE_DEFAULTS[engine]["user"]


def _compute_mongo_fcv_chain(source_version: str, target_version: str) -> list:
    """Return the full hop chain for a MongoDB upgrade, including source and target."""
    seq = _MONGO_VERSION_SEQUENCE
    if source_version not in seq:
        raise ValueError(
            f"{source_version!r} not in supported MongoDB versions: {seq}"
        )
    if target_version not in seq:
        raise ValueError(
            f"{target_version!r} not in supported MongoDB versions: {seq}"
        )
    src_idx = seq.index(source_version)
    tgt_idx = seq.index(target_version)
    if tgt_idx <= src_idx:
        raise ValueError(
            f"target_version {target_version!r} must be newer than source_version {source_version!r}"
        )
    return seq[src_idx : tgt_idx + 1]


def _resolve_params(parameters: dict, engine: str) -> dict:
    """Resolve defaults for optional parameters."""
    return {
        "engine": engine,
        "source_version": parameters.get("source_version"),
        "target_version": parameters["target_version"],
        "strategy": parameters.get("strategy", "dump_restore"),
        "db_host": parameters.get("db_host", "localhost"),
        "db_port": parameters.get("db_port", _default_port(engine)),
        "db_name": parameters.get("db_name"),
        "db_user": parameters.get("db_user", _default_user(engine)),
        "db_password": parameters.get("db_password"),
        "rds_instance_id": parameters.get("rds_instance_id"),
        "snapshot_s3_bucket": parameters.get("snapshot_s3_bucket"),
        "health_check_url": parameters.get("health_check_url"),
        "dry_run": bool(parameters.get("dry_run", False)),
        "skip_snapshot": bool(parameters.get("skip_snapshot", False)),
        "mongo_fcv_chain": parameters.get("mongo_fcv_chain"),
    }


# Lazy import to allow unit testing without full app context
def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


# Module-level alias patched in unit tests
async def dispatch_agent_job(command, parameters, asset_ids, timeout_seconds=300):
    fn = _get_dispatch()
    return await fn(
        command=command,
        parameters=parameters,
        asset_ids=asset_ids,
        timeout_seconds=timeout_seconds,
    )


def _boto3_rds(creds: dict):
    import boto3
    return boto3.client(
        "rds",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )


def _boto3_ec2(creds: dict):
    import boto3
    return boto3.client(
        "ec2",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )


def _boto3_s3(creds: dict):
    import boto3
    return boto3.client(
        "s3",
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Main entry point. Runs preflight -> snapshot -> upgrade -> verify sequentially."""
    if not asset_ids:
        raise ValueError("asset_ids required")

    engine = parameters.get("engine")
    if engine not in _ENGINE_DEFAULTS:
        raise ValueError(f"engine must be one of {list(_ENGINE_DEFAULTS)} — got {engine!r}")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters, engine)

    if p["skip_snapshot"]:
        import uuid as _uuid
        try:
            _asset_uuid = _uuid.UUID(asset_id)
        except (ValueError, AttributeError):
            _asset_uuid = None
        if _asset_uuid is not None:
            async with AsyncSessionLocal() as db:
                _asset = await db.get(Asset, _asset_uuid)
                if _asset and (_asset.asset_metadata or {}).get("environment") == "prod":
                    raise RuntimeError(
                        "skip_snapshot=True is not permitted on prod assets. "
                        "Remove skip_snapshot or override environment tag."
                    )
        else:
            # Asset ID not a UUID — check via mock (for testing) or skip
            async with AsyncSessionLocal() as db:
                _asset = await db.get(Asset, asset_id)
                if _asset and (_asset.asset_metadata or {}).get("environment") == "prod":
                    raise RuntimeError(
                        "skip_snapshot=True is not permitted on prod assets. "
                        "Remove skip_snapshot or override environment tag."
                    )

    # --- Phase 1: Pre-flight ---
    preflight_result = await _preflight(asset_id, p)
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return preflight_result

    # --- Phase 2: Snapshot ---
    snapshot_result = {}
    if not p["skip_snapshot"]:
        snapshot_result = await _take_snapshot(asset_id, p, connector)
    else:
        logger.warning(f"skip_snapshot=True for asset {asset_id} — no rollback artifact")
        snapshot_result = {"snapshot_type": "skipped", "snapshot_id": None}

    # --- Phase 3: Upgrade ---
    try:
        upgrade_result = await _upgrade(asset_id, p, connector)
    except Exception as exc:
        logger.error(f"Upgrade failed for asset {asset_id}: {exc}")
        return {
            "status": "upgrade_failed",
            "error": str(exc),
            "snapshot_result": snapshot_result,
            "upgrade_result": None,
        }

    # --- Phase 4: Verify ---
    verify_result = await _verify(asset_id, p, connector)
    verify_status = verify_result.get("verify_status", "failed")

    return {
        "status": "completed" if verify_status == "passed" else "verify_failed",
        "engine": engine,
        "source_version": p["source_version"],
        "target_version": p["target_version"],
        "snapshot_result": snapshot_result,
        "upgrade_result": upgrade_result,
        "verify_result": verify_result,
        "asset_id": asset_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore from pre-upgrade snapshot."""
    snapshot_result = execution_result.get("snapshot_result") or {}
    snapshot_type = snapshot_result.get("snapshot_type")

    if not snapshot_type or snapshot_type == "skipped" or not snapshot_result.get("snapshot_id"):
        return {"rolled_back": False, "reason": "no_snapshot_available"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )

    try:
        if snapshot_type == "rds_snapshot":
            return await _rollback_rds(asset_id, snapshot_result, connector)
        elif snapshot_type == "s3_dump":
            return await _rollback_s3_dump(asset_id, snapshot_result, parameters, connector)
        else:
            # EBS snapshot (in_place postgres, mysql, mongodb)
            return await _rollback_ebs(asset_id, snapshot_result, connector)
    except Exception as exc:
        logger.error(f"Rollback failed for asset {asset_id}: {exc}")
        return {
            "rolled_back": False,
            "reason": str(exc),
            "snapshot_result": snapshot_result,
        }


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

async def _preflight(asset_id: str, p: dict) -> dict:
    result = await dispatch_agent_job(
        command="db_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    return result


async def _take_snapshot(asset_id: str, p: dict, connector, cr_id: str = "unknown") -> dict:
    """Take pre-upgrade snapshot. Strategy:
      - rds_instance_id set -> RDS snapshot API
      - engine=postgres and strategy=dump_restore and snapshot_s3_bucket set -> pg_dumpall to S3
      - otherwise -> EBS snapshot (reuse os_upgrade pattern)
    """
    creds = getattr(connector, "credentials", {}) or {}
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cr_short = cr_id[:8]

    rds_instance_id = p.get("rds_instance_id")
    snapshot_s3_bucket = p.get("snapshot_s3_bucket")
    engine = p["engine"]
    strategy = p.get("strategy", "dump_restore")

    if rds_instance_id:
        # --- RDS snapshot ---
        rds = _boto3_rds(creds)
        snap_id = f"nexplane-preupgrade-{cr_short}-{timestamp}"
        loop = asyncio.get_event_loop()
        resp = rds.create_db_snapshot(
            DBInstanceIdentifier=rds_instance_id,
            DBSnapshotIdentifier=snap_id,
            Tags=[
                {"Key": "nexplane-purpose", "Value": "pre-db-upgrade"},
                {"Key": "nexplane-asset-id", "Value": asset_id},
                {"Key": "nexplane-cr-id", "Value": cr_id},
            ],
        )
        snap_arn = resp["DBSnapshot"].get("DBSnapshotArn", snap_id)
        logger.info(f"RDS snapshot {snap_id} creating...")
        await loop.run_in_executor(
            None,
            lambda: rds.get_waiter("db_snapshot_available").wait(
                DBSnapshotIdentifier=snap_id,
                WaiterConfig={"Delay": 30, "MaxAttempts": 180},  # 90 min max
            ),
        )
        logger.info(f"RDS snapshot {snap_id} available")
        return {
            "snapshot_type": "rds_snapshot",
            "snapshot_id": snap_id,
            "snapshot_arn": snap_arn,
            "rds_instance_id": rds_instance_id,
            "snapshot_completed_at": datetime.now(timezone.utc).isoformat(),
        }

    if engine == "postgres" and strategy == "dump_restore" and snapshot_s3_bucket:
        # --- pg_dumpall to S3 ---
        s3_key = f"nexplane-{cr_short}-{timestamp}.dump.gz"
        dump_cmd = (
            f"pg_dumpall -U {p['db_user']} -p {p['db_port']} | "
            f"gzip | aws s3 cp - s3://{snapshot_s3_bucket}/{s3_key}"
        )
        await dispatch_agent_job(
            command="db_dump_to_s3",
            parameters={"command": dump_cmd, "db_password": p.get("db_password")},
            asset_ids=[asset_id],
            timeout_seconds=3600,
        )
        # Verify object exists and is non-empty
        s3 = _boto3_s3(creds)
        head = s3.head_object(Bucket=snapshot_s3_bucket, Key=s3_key)
        if head.get("ContentLength", 0) == 0:
            raise RuntimeError(f"S3 dump {s3_key} is empty — snapshot failed")
        logger.info(f"S3 dump snapshot at s3://{snapshot_s3_bucket}/{s3_key}")
        return {
            "snapshot_type": "s3_dump",
            "snapshot_id": s3_key,
            "snapshot_s3_key": s3_key,
            "snapshot_s3_bucket": snapshot_s3_bucket,
            "snapshot_completed_at": datetime.now(timezone.utc).isoformat(),
        }

    # --- EBS snapshot (in_place postgres, mysql, mongodb) ---
    from app.connectors.executors.nexplane_agent.os_upgrade import _take_snapshot as _ebs_snap
    from app.database import AsyncSessionLocal
    from app.models.asset import Asset
    import uuid

    async with AsyncSessionLocal() as db:
        asset = await db.get(Asset, uuid.UUID(asset_id))
        instance_id = (asset.asset_metadata or {}).get("instance_id") if asset else None
        if asset and (asset.asset_metadata or {}).get("environment") == "prod" and p.get("skip_snapshot"):
            raise RuntimeError(
                "skip_snapshot=True is not permitted on prod assets. "
                "Remove skip_snapshot or override environment tag."
            )

    if not instance_id:
        raise RuntimeError(
            f"Cannot take EBS snapshot: no instance_id in asset metadata for {asset_id}. "
            "Set rds_instance_id or snapshot_s3_bucket for non-EBS deployments."
        )

    snap_meta = await _ebs_snap(asset_id, instance_id, connector)
    return {
        "snapshot_type": "ebs_snapshot",
        "snapshot_id": snap_meta["snapshot_id"],
        "snapshot_meta": snap_meta,
        "snapshot_completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _upgrade(asset_id: str, p: dict, connector) -> dict:
    """Dispatch engine-specific upgrade commands."""
    engine = p["engine"]

    if engine == "postgres":
        strategy = p.get("strategy", "dump_restore")
        if strategy == "dump_restore":
            result = await dispatch_agent_job(
                command="db_upgrade_postgres_dump_restore",
                parameters=p,
                asset_ids=[asset_id],
                timeout_seconds=7200,
            )
        else:
            result = await dispatch_agent_job(
                command="db_upgrade_postgres_in_place",
                parameters=p,
                asset_ids=[asset_id],
                timeout_seconds=7200,
            )
        return {
            "upgrade_status": "completed",
            "engine": engine,
            "strategy": strategy,
            "agent_result": result,
            "upgraded_at": datetime.now(timezone.utc).isoformat(),
        }

    elif engine == "mysql":
        result = await dispatch_agent_job(
            command="db_upgrade_mysql",
            parameters=p,
            asset_ids=[asset_id],
            timeout_seconds=3600,
        )
        return {
            "upgrade_status": "completed",
            "engine": engine,
            "agent_result": result,
            "upgraded_at": datetime.now(timezone.utc).isoformat(),
        }

    elif engine == "mongodb":
        return await _mongo_fcv_chain(asset_id, p)

    else:
        raise ValueError(f"Unknown engine: {engine!r}")


async def _mongo_fcv_chain(asset_id: str, p: dict) -> dict:
    """Execute sequential MongoDB FCV bumps. All hops in one CR execution."""
    source = p.get("source_version")
    target = p["target_version"]
    chain = p.get("mongo_fcv_chain") or _compute_mongo_fcv_chain(source, target)

    completed_hops = []
    for i in range(len(chain) - 1):
        from_ver = chain[i]
        to_ver = chain[i + 1]
        hop_label = f"{from_ver}->{to_ver}"
        logger.info(f"MongoDB FCV hop: {hop_label}")
        await dispatch_agent_job(
            command="db_upgrade_mongo_fcv_hop",
            parameters={**p, "from_version": from_ver, "to_version": to_ver},
            asset_ids=[asset_id],
            timeout_seconds=1800,
        )
        completed_hops.append(hop_label)
        logger.info(f"MongoDB FCV hop completed: {hop_label}")

    return {
        "upgrade_status": "completed",
        "engine": "mongodb",
        "fcv_chain": chain,
        "completed_hops": completed_hops,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _http_get(url: str, timeout: float = 10.0):
    """Thin async HTTP GET wrapper — patched in tests."""
    import httpx
    async with httpx.AsyncClient() as client:
        return await client.get(url, timeout=timeout)


async def _verify(asset_id: str, p: dict, connector) -> dict:
    """Run DB smoke query and optional health check URL."""
    engine = p["engine"]
    target_version = p["target_version"]

    smoke_commands = {
        "postgres": "SELECT version();",
        "mysql":    "SELECT @@version;",
        "mongodb":  '{"serverStatus": 1}',
    }

    result = await dispatch_agent_job(
        command="db_version_query",
        parameters={
            "engine": engine,
            "query": smoke_commands[engine],
            "db_user": p.get("db_user"),
            "db_password": p.get("db_password"),
            "db_port": p.get("db_port"),
            "db_host": p.get("db_host", "localhost"),
        },
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    stdout = result.get("stdout", "")
    smoke_ok = target_version in stdout
    verify_result = {
        "verify_status": "passed" if smoke_ok else "failed",
        "db_version_confirmed": stdout.strip()[:200],
        "smoke_query_ok": smoke_ok,
    }

    # Optional HTTP health check — 3 attempts with 10s backoff
    health_check_url = p.get("health_check_url")
    if health_check_url:
        attempts = 0
        hc_status = None
        for attempt in range(3):
            attempts += 1
            try:
                resp = await _http_get(health_check_url, timeout=10.0)
                hc_status = resp.status_code
                if 200 <= hc_status < 300:
                    break
            except Exception as exc:
                logger.warning(f"Health check attempt {attempt + 1} failed: {exc}")
            if attempt < 2:
                await asyncio.sleep(10)

        verify_result.update({
            "health_check_url": health_check_url,
            "health_check_status": hc_status,
            "health_check_attempts": attempts,
        })
        if not (hc_status and 200 <= hc_status < 300):
            verify_result["verify_status"] = "failed"

    if not smoke_ok:
        verify_result["verify_status"] = "failed"

    return verify_result


async def _rollback_rds(asset_id: str, snapshot_result: dict, connector) -> dict:
    """Restore RDS instance from pre-upgrade snapshot."""
    creds = getattr(connector, "credentials", {}) or {}
    rds = _boto3_rds(creds)
    loop = asyncio.get_event_loop()

    snap_id = snapshot_result["snapshot_id"]
    rds_instance_id = snapshot_result["rds_instance_id"]
    restored_id = rds_instance_id + "-restored"

    logger.info(f"Restoring RDS {rds_instance_id} from snapshot {snap_id} as {restored_id}")
    rds.restore_db_instance_from_db_snapshot(
        DBSnapshotIdentifier=snap_id,
        DBInstanceIdentifier=restored_id,
        Tags=[
            {"Key": "nexplane-rollback-source", "Value": rds_instance_id},
            {"Key": "nexplane-rollback-orphan", "Value": "pending"},
        ],
    )
    await loop.run_in_executor(
        None,
        lambda: rds.get_waiter("db_instance_available").wait(
            DBInstanceIdentifier=restored_id,
            WaiterConfig={"Delay": 30, "MaxAttempts": 120},
        ),
    )
    logger.info(f"Restored instance {restored_id} available")

    # Tag original as orphan for operator cleanup
    try:
        rds.add_tags_to_resource(
            ResourceName=rds_instance_id,
            Tags=[{"Key": "nexplane-rollback-orphan", "Value": "true"}],
        )
    except Exception as exc:
        logger.warning(f"Could not tag original RDS instance: {exc}")

    return {
        "rolled_back": True,
        "strategy": "rds_snapshot_restore",
        "snapshot_id": snap_id,
        "restored_instance_id": restored_id,
        "notes": "Original instance still exists; tag nexplane-rollback-orphan=true; decommission manually.",
    }


async def _rollback_s3_dump(
    asset_id: str, snapshot_result: dict, parameters: dict, connector
) -> dict:
    """Restore Postgres cluster from pg_dumpall S3 dump."""
    s3_key = snapshot_result["snapshot_s3_key"]
    bucket = snapshot_result["snapshot_s3_bucket"]
    p = parameters.get("desired_outcome") or parameters

    restore_cmd = (
        f"aws s3 cp s3://{bucket}/{s3_key} - | gunzip | "
        f"psql -U {p.get('db_user', 'postgres')} -p {p.get('db_port', 5432)}"
    )
    await dispatch_agent_job(
        command="db_restore_from_s3_dump",
        parameters={
            "command": restore_cmd,
            "db_password": p.get("db_password"),
            "db_user": p.get("db_user", "postgres"),
        },
        asset_ids=[asset_id],
        timeout_seconds=7200,
    )
    return {
        "rolled_back": True,
        "strategy": "s3_dump_restore",
        "snapshot_s3_key": s3_key,
        "snapshot_s3_bucket": bucket,
    }


async def _rollback_ebs(asset_id: str, snapshot_result: dict, connector) -> dict:
    """EBS volume swap rollback — reuses os_upgrade._restore_snapshot pattern."""
    from app.connectors.executors.nexplane_agent.os_upgrade import _restore_snapshot

    snap_id = snapshot_result["snapshot_id"]
    snap_meta = snapshot_result.get("snapshot_meta", {})

    if not snap_meta.get("instance_id"):
        return {
            "rolled_back": False,
            "reason": "snapshot_meta missing instance_id — cannot perform automated EBS restore",
            "snapshot_id": snap_id,
            "manual_steps": [
                "1. Stop the EC2 instance",
                f"2. aws ec2 create-volume --snapshot-id {snap_id} --availability-zone <az>",
                "3. Detach current data volume",
                "4. Attach restored volume at original device name",
                "5. Start instance",
            ],
        }

    result = await _restore_snapshot(asset_id, snap_id, snap_meta, connector)
    return {
        "rolled_back": result.get("agent_recovered", False),
        "strategy": "ebs_volume_swap",
        "snapshot_id": snap_id,
        "new_volume_id": result.get("new_volume_id"),
        "old_volume_id": result.get("old_volume_id"),
        "agent_recovered": result.get("agent_recovered"),
        "notes": "Old volume tagged nexplane-rollback-orphan for operator cleanup",
    }
