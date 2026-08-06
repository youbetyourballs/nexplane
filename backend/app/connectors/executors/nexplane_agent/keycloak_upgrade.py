# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Keycloak major version upgrade executor.
Flow: preflight -> snapshot (realm export + DB dump) -> upgrade -> verify -> (rollback).
Paths:
  - WildFly->Quarkus (source <= 16, target >= 17): export realms, install new binary, import.
  - Quarkus in-place (source >= 17, target >= 17): export, stop, replace binary, build, start.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_DB_VENDOR_DEFAULTS = {
    "postgres": {"port": 5432},
    "mysql":    {"port": 3306},
    "h2":       {"port": None},
}


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    db_vendor = p.get("db_vendor", "postgres")
    return {
        "source_version":   p.get("source_version"),
        "target_version":   p["target_version"],
        "keycloak_home":    p.get("keycloak_home", "/opt/keycloak"),
        "db_vendor":        db_vendor,
        "db_host":          p.get("db_host", "localhost"),
        "db_port":          p.get("db_port", _DB_VENDOR_DEFAULTS.get(db_vendor, {}).get("port")),
        "db_name":          p.get("db_name"),
        "db_user":          p.get("db_user"),
        "db_password":      p.get("db_password"),
        "admin_user":       p.get("admin_user", "admin"),
        "admin_password":   p.get("admin_password"),
        "realms_to_export": p.get("realms_to_export"),
        "dry_run":          bool(p.get("dry_run", False)),
    }


def _migration_path(source_version: str, target_version: str) -> str:
    """Return 'wildfly_to_quarkus' or 'quarkus_inplace'."""
    try:
        src_major = int(str(source_version).split(".")[0])
    except (ValueError, AttributeError):
        src_major = 17
    if src_major <= 16:
        return "wildfly_to_quarkus"
    return "quarkus_inplace"


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


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Main entry point. preflight -> snapshot -> upgrade -> verify."""
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    if not p.get("source_version"):
        raise ValueError("source_version is required")
    if not p.get("target_version"):
        raise ValueError("target_version is required")

    path = _migration_path(p["source_version"], p["target_version"])
    logger.info(f"Keycloak upgrade path: {path} ({p['source_version']} -> {p['target_version']})")

    # --- Phase 1: Preflight ---
    preflight_result = await _preflight(asset_id, p)
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return {**preflight_result, "migration_path": path, "dry_run": True}

    # --- Phase 2: Snapshot ---
    snapshot_result = await _snapshot(asset_id, p)

    # --- Phase 3: Upgrade ---
    try:
        if path == "wildfly_to_quarkus":
            upgrade_result = await _upgrade_wildfly_to_quarkus(asset_id, p)
        else:
            upgrade_result = await _upgrade_quarkus_inplace(asset_id, p)
    except Exception as exc:
        logger.error(f"Keycloak upgrade failed: {exc}")
        return {
            "status": "upgrade_failed",
            "error": str(exc),
            "snapshot_result": snapshot_result,
        }

    # --- Phase 4: Verify ---
    verify_result = await _verify(asset_id, p)

    return {
        "status": "completed" if verify_result.get("verify_status") == "passed" else "verify_failed",
        "migration_path": path,
        "source_version": p["source_version"],
        "target_version": p["target_version"],
        "snapshot_result": snapshot_result,
        "upgrade_result": upgrade_result,
        "verify_result": verify_result,
        "asset_id": asset_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore old Keycloak binary + DB dump."""
    snapshot_result = execution_result.get("snapshot_result") or {}
    if not snapshot_result.get("realm_export_paths") and not snapshot_result.get("db_dump_path"):
        return {"rolled_back": False, "reason": "no_snapshot_available"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="keycloak_rollback",
            parameters={
                **p,
                "snapshot_result": snapshot_result,
                "migration_path": execution_result.get("migration_path", "quarkus_inplace"),
            },
            asset_ids=[asset_id],
            timeout_seconds=900,
        )
        return {
            "rolled_back": True,
            "strategy": "binary_restore_and_db_reimport",
            "agent_result": result,
        }
    except Exception as exc:
        logger.error(f"Keycloak rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}


# ---------------------------------------------------------------------------
# Phase implementations
# ---------------------------------------------------------------------------

async def _preflight(asset_id: str, p: dict) -> dict:
    return await dispatch_agent_job(
        command="keycloak_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )


async def _snapshot(asset_id: str, p: dict) -> dict:
    """Export all realms and dump the DB."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Realm export
    realm_result = await dispatch_agent_job(
        command="keycloak_export_realms",
        parameters={
            **p,
            "export_dir": f"/tmp/nexplane-kc-export-{timestamp}",
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    realm_export_paths = realm_result.get("realm_export_paths", [])

    # DB dump (skip for h2 — embedded, backed up via binary copy)
    db_dump_path = None
    if p.get("db_vendor") != "h2":
        dump_result = await dispatch_agent_job(
            command="keycloak_db_dump",
            parameters={
                **p,
                "dump_path": f"/tmp/nexplane-kc-db-{timestamp}.sql.gz",
            },
            asset_ids=[asset_id],
            timeout_seconds=600,
        )
        db_dump_path = dump_result.get("dump_path")

    return {
        "snapshot_type": "realm_export_and_db_dump",
        "realm_export_paths": realm_export_paths,
        "db_dump_path": db_dump_path,
        "snapshot_completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def _upgrade_wildfly_to_quarkus(asset_id: str, p: dict) -> dict:
    """WildFly -> Quarkus migration path (source <= 16, target >= 17)."""
    # 1. Download and extract new Keycloak release
    await dispatch_agent_job(
        command="keycloak_install_release",
        parameters={**p, "install_path": f"/opt/keycloak-{p['target_version']}"},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 2. Stop old WildFly-based Keycloak
    await dispatch_agent_job(
        command="keycloak_stop_service",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    # 3. Build Quarkus distribution
    await dispatch_agent_job(
        command="keycloak_build_quarkus",
        parameters={**p, "install_path": f"/opt/keycloak-{p['target_version']}"},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 4. Configure and start new Keycloak
    await dispatch_agent_job(
        command="keycloak_configure_and_start",
        parameters={**p, "install_path": f"/opt/keycloak-{p['target_version']}"},
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    # 5. Import realms
    await dispatch_agent_job(
        command="keycloak_import_realms",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    return {
        "upgrade_status": "completed",
        "migration_path": "wildfly_to_quarkus",
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _upgrade_quarkus_inplace(asset_id: str, p: dict) -> dict:
    """Quarkus in-place upgrade (source >= 17, target >= 17)."""
    # 1. Stop running Keycloak
    await dispatch_agent_job(
        command="keycloak_stop_service",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    # 2. Download and extract new release over keycloak_home
    await dispatch_agent_job(
        command="keycloak_install_release",
        parameters={**p, "install_path": p["keycloak_home"]},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 3. Build optimized distribution
    await dispatch_agent_job(
        command="keycloak_build_quarkus",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    # 4. Start (DB schema auto-migration runs on first start)
    await dispatch_agent_job(
        command="keycloak_start_optimized",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    return {
        "upgrade_status": "completed",
        "migration_path": "quarkus_inplace",
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _verify(asset_id: str, p: dict) -> dict:
    """Poll /health/ready and check /admin/realms via admin API."""
    result = await dispatch_agent_job(
        command="keycloak_verify",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    health_ok = result.get("health_ready", False)
    version_ok = p["target_version"] in str(result.get("version", ""))
    return {
        "verify_status": "passed" if (health_ok and version_ok) else "failed",
        "health_ready": health_ok,
        "version_confirmed": result.get("version"),
        "realm_count": result.get("realm_count"),
    }
