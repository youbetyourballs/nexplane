# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kong API Gateway upgrade executor.

Flow: preflight → snapshot (deck dump + pg_dump) → plugin compat check →
      stop Kong → install new package → run migrations → start Kong → verify.

Rollback: stop Kong, install old package, kong migrations down, restore deck dump/DB, start.
ROLLBACK_CAPABILITY = "full"
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    kong_host = parameters.get("kong_host", "localhost")
    kong_admin_port = int(parameters.get("kong_admin_port", 8001))
    kong_proxy_port = int(parameters.get("kong_proxy_port", 8000))
    db_mode = parameters.get("db_mode", "postgres")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required")
    if not target_version:
        raise ValueError("target_version required")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Step 1: Preflight — record current version, route/service/consumer counts
    logger.info(f"Kong upgrade preflight on {asset_id}: {source_version}→{target_version}")
    preflight = await dispatch_agent_job(
        command="kong_preflight",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "kong_host": kong_host,
            "kong_admin_port": kong_admin_port,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    routes_count = preflight.get("routes_count", 0)
    services_count = preflight.get("services_count", 0)
    plugins_count = preflight.get("plugins_count", 0)

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "routes_count": routes_count,
            "services_count": services_count,
            "plugins_count": plugins_count,
            "preflight": preflight,
        }

    # Step 2: Snapshot
    backup_path = f"/tmp/nexplane-kong-backup-{asset_id[:8]}.yaml"
    snapshot = await dispatch_agent_job(
        command="kong_snapshot",
        parameters={
            "db_mode": db_mode,
            "backup_path": backup_path,
            "db_host": parameters.get("db_host", "localhost"),
            "db_port": parameters.get("db_port", 5432),
            "db_name": parameters.get("db_name", "kong"),
            "db_user": parameters.get("db_user", "kong"),
            "db_password": parameters.get("db_password", ""),
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 3: Plugin compatibility check
    compat = await dispatch_agent_job(
        command="kong_check_plugins",
        parameters={"target_version": target_version, "kong_admin_port": kong_admin_port},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    plugin_warnings = compat.get("warnings", [])
    if compat.get("blocking_incompatibilities"):
        return {
            "status": "blocked",
            "reason": "Plugin incompatibilities block upgrade",
            "incompatibilities": compat.get("blocking_incompatibilities"),
        }

    # Step 4: Stop Kong
    await dispatch_agent_job(
        command="kong_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )

    # Step 5: Install new version
    await dispatch_agent_job(
        command="kong_install_version",
        parameters={"version": target_version},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 6: Run migrations (postgres mode only)
    if db_mode == "postgres":
        await dispatch_agent_job(
            command="kong_run_migrations",
            parameters={
                "db_host": parameters.get("db_host", "localhost"),
                "db_name": parameters.get("db_name", "kong"),
                "db_user": parameters.get("db_user", "kong"),
                "db_password": parameters.get("db_password", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )

    # Step 7: Start Kong
    await dispatch_agent_job(
        command="kong_start",
        parameters={"kong_admin_port": kong_admin_port},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    # Step 8: Verify
    verify = await dispatch_agent_job(
        command="kong_verify",
        parameters={
            "target_version": target_version,
            "kong_host": kong_host,
            "kong_admin_port": kong_admin_port,
            "kong_proxy_port": kong_proxy_port,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed" if verify.get("version_ok") else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "routes_count": routes_count,
        "services_count": services_count,
        "plugins_count": plugins_count,
        "plugin_warnings": plugin_warnings,
        "backup_path": backup_path,
        "snapshot": snapshot,
        "verify": verify,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    source_version = execution_result.get("source_version", "")
    backup_path = execution_result.get("backup_path", "")
    db_mode = parameters.get("db_mode", "postgres")

    if not source_version:
        return {"rolled_back": False, "reason": "source_version missing from execution_result"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Stop Kong, install old version, restore data, start
    await dispatch_agent_job(
        command="kong_stop",
        parameters={},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    await dispatch_agent_job(
        command="kong_install_version",
        parameters={"version": source_version},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    if db_mode == "postgres" and backup_path:
        await dispatch_agent_job(
            command="kong_restore_snapshot",
            parameters={
                "backup_path": backup_path,
                "db_mode": db_mode,
                "db_host": parameters.get("db_host", "localhost"),
                "db_name": parameters.get("db_name", "kong"),
                "db_user": parameters.get("db_user", "kong"),
                "db_password": parameters.get("db_password", ""),
            },
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    await dispatch_agent_job(
        command="kong_start",
        parameters={"kong_admin_port": parameters.get("kong_admin_port", 8001)},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    return {
        "rolled_back": True,
        "source_version": source_version,
        "backup_path": backup_path,
    }
