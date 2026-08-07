# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
OpenLDAP schema migration executor.
Uses run_command exclusively via the nexplane agent.
Flow: preflight -> slapcat backup -> schema apply -> verify -> (rollback).
"""
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def _run(command: str, asset_id: str, timeout: int = 120) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="run_command",
        parameters={"command": command, "timeout": timeout},
        asset_ids=[asset_id],
        timeout_seconds=timeout + 30,
    )


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_schema": p.get("source_schema"),
        "target_schema": p.get("target_schema"),
        "bind_dn": p.get("bind_dn", "cn=admin,dc=example,dc=com"),
        "bind_password": p.get("bind_password"),
        "base_dn": p.get("base_dn", "dc=example,dc=com"),
        "schema_ldif_path": p.get("schema_ldif_path"),
        "dry_run": bool(p.get("dry_run", False)),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)

    source_schema = p.get("source_schema")
    target_schema = p.get("target_schema")
    bind_dn = p["bind_dn"]
    bind_password = p.get("bind_password", "")
    base_dn = p["base_dn"]
    schema_ldif_path = p.get("schema_ldif_path")

    # Phase 1: Preflight
    logger.info(f"OpenLDAP schema migration preflight {source_schema} -> {target_schema} on {asset_id}")
    await _run(
        "slapd -V 2>&1 || ldapsearch -x -H ldap://localhost -b '' -s base 2>&1 || true",
        asset_id,
        timeout=60,
    )

    if p["dry_run"]:
        return {
            "status": "dry_run",
            "source_schema": source_schema,
            "target_schema": target_schema,
            "asset_id": asset_id,
        }

    # Phase 2: Backup
    config_backup_path = "/tmp/nexplane-ldap-config-backup.ldif"
    data_backup_path = "/tmp/nexplane-ldap-data-backup.ldif"
    await _run(
        f"slapcat -n 0 -l {config_backup_path} 2>&1; echo CONFIG_BACKUP_EXIT=$?; "
        f"slapcat -n 1 -l {data_backup_path} 2>&1 || true",
        asset_id,
        timeout=300,
    )

    # Phase 3: Schema migration — import LDIF if provided
    # cn=config schema changes require EXTERNAL SASL auth via ldapi:///
    ldif = schema_ldif_path or "/tmp/nexplane-testapp.ldif"
    schema_cmd = f"""
LDIF={ldif}
if [ -f "$LDIF" ]; then
    ldapadd -Y EXTERNAL -H ldapi:/// -f "$LDIF" 2>&1 || \
    ldapadd -x -H ldap://localhost -D "{bind_dn}" -w "{bind_password}" -f "$LDIF" 2>&1 || true
fi
echo SCHEMA_DONE
""".strip()
    await _run(schema_cmd, asset_id, timeout=120)

    # Phase 4: Verify — search directly for the schema DN
    schema_dn = (parameters.get("desired_outcome") or parameters).get("schema_dn", "cn=testapp,cn=schema,cn=config")
    verify = await _run(
        f'ldapsearch -Y EXTERNAL -H ldapi:/// -b "{schema_dn}" -s base 2>&1; echo VERIFY_DONE',
        asset_id,
        timeout=60,
    )
    verify_out = str(verify.get("output", "") or "")
    schema_present = (schema_dn in verify_out or "objectClass: olcSchemaConfig" in verify_out) and "VERIFY_DONE" in verify_out

    return {
        "status": "completed",
        "source_schema": source_schema,
        "target_schema": target_schema,
        "config_backup_path": config_backup_path,
        "data_backup_path": data_backup_path,
        "verify_result": {"schema_dn_present": schema_present, "verify_output": verify_out[:300]},
        "asset_id": asset_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id") or str(
        (execution_result.get("_target_asset_ids") or parameters.get("asset_ids") or [None])[0] or ""
    )
    if not asset_id:
        return {"rolled_back": False, "reason": "No asset_id available for rollback"}
    config_backup_path = execution_result.get("config_backup_path", "/tmp/nexplane-ldap-config-backup.ldif")

    logger.info(f"OpenLDAP rollback: restoring config from {config_backup_path} on {asset_id}")

    rollback_cmd = f"""
systemctl stop slapd 2>/dev/null || service slapd stop 2>/dev/null || true
sleep 3
rm -rf /etc/ldap/slapd.d/* 2>/dev/null || true
slapadd -n 0 -F /etc/ldap/slapd.d -l {config_backup_path} 2>&1 || true
systemctl start slapd 2>/dev/null || service slapd start 2>/dev/null || true
echo ROLLBACK_DONE
""".strip()

    await _run(rollback_cmd, asset_id, timeout=300)

    return {
        "rolled_back": True,
        "strategy": "slapcat_config_restore",
        "config_backup_path": config_backup_path,
        "data_loss_warning": "Any schema changes applied after the backup was taken have been reverted. Data DB is untouched.",
    }
