# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OpenLDAP schema migration executor.
Flow: preflight -> slapcat backup -> schema test (temp dir) -> apply -> verify -> (rollback).
Rollback: stop slapd, restore config DB from slapcat export, start slapd.
Data DB is untouched by schema changes.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "schema_ldif_path": p["schema_ldif_path"],
        "schema_dn":        p["schema_dn"],
        "backup_path":      p.get("backup_path", "/tmp/nexplane-ldap-backup.ldif"),
        "slapd_config_dir": p.get("slapd_config_dir", "/etc/ldap/slapd.d"),
        "dry_run":          bool(p.get("dry_run", False)),
    }


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
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    # --- Phase 1: Preflight ---
    preflight_result = await dispatch_agent_job(
        command="openldap_preflight",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    if preflight_result.get("status") == "preflight_blocked":
        return preflight_result

    if p["dry_run"]:
        return {**preflight_result, "dry_run": True}

    # --- Phase 2: slapcat backup ---
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    config_backup_path = f"/tmp/nexplane-ldap-config-{timestamp}.ldif"
    data_backup_path   = f"/tmp/nexplane-ldap-data-{timestamp}.ldif"

    await dispatch_agent_job(
        command="openldap_slapcat",
        parameters={
            **p,
            "config_backup_path": config_backup_path,
            "data_backup_path":   data_backup_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    logger.info(f"slapcat backup: config={config_backup_path} data={data_backup_path}")

    # --- Phase 3: Schema test in temp config dir ---
    await dispatch_agent_job(
        command="openldap_schema_test",
        parameters={**p, "config_backup_path": config_backup_path},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    logger.info("Schema LDIF validated in temp config dir")

    # --- Phase 4: Apply schema ---
    try:
        await dispatch_agent_job(
            command="openldap_schema_apply",
            parameters=p,
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
    except Exception as exc:
        logger.error(f"Schema apply failed: {exc}")
        return {
            "status":              "upgrade_failed",
            "error":               str(exc),
            "config_backup_path":  config_backup_path,
            "data_backup_path":    data_backup_path,
        }

    # --- Phase 5: Verify ---
    verify_result = await dispatch_agent_job(
        command="openldap_verify_schema",
        parameters=p,
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    schema_present = verify_result.get("schema_dn_present", False)

    return {
        "status":             "completed" if schema_present else "verify_failed",
        "schema_dn":          p["schema_dn"],
        "config_backup_path": config_backup_path,
        "data_backup_path":   data_backup_path,
        "verify_result":      verify_result,
        "asset_id":           asset_id,
        "applied_at":         datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Stop slapd, restore config DB from slapcat backup, start slapd."""
    config_backup_path = execution_result.get("config_backup_path")
    if not config_backup_path:
        return {"rolled_back": False, "reason": "no config_backup_path in execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)

    try:
        result = await dispatch_agent_job(
            command="openldap_config_restore",
            parameters={**p, "config_backup_path": config_backup_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
        return {
            "rolled_back":        True,
            "strategy":           "slapcat_config_restore",
            "config_backup_path": config_backup_path,
            "notes":              "Data DB untouched; only config DB (schema) restored.",
            "agent_result":       result,
        }
    except Exception as exc:
        logger.error(f"OpenLDAP rollback failed: {exc}")
        return {"rolled_back": False, "reason": str(exc)}
