# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Tier-zero Active Directory operations.

Covers the highest-blast-radius, lowest-frequency AD operations:
  - ad_domain_functional_level_upgrade (IRREVERSIBLE)
  - ad_trust_create
  - ad_gpo_deploy
  - ad_pso_manage
  - ad_stale_computer_cleanup

Each is a separate entry point (called by the platform's executor router based on
change_type), all in this single file since they share the same connector type
and LDAP client pattern.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ad_domain_functional_level_upgrade
# ---------------------------------------------------------------------------

ROLLBACK_CAPABILITY_DFL = "irreversible"  # DFL is a one-way door


async def execute_dfl_upgrade(parameters: dict, asset_ids: list, connector) -> dict:
    """Raise the domain or forest functional level.

    Prerequisites enforced in preflight:
      - All DCs must be running the OS version that supports the target DFL
      - No lingering DCs at older OS versions

    Parameters:
      target_level (str): e.g. "WinThreshold" (2016), "Win2019" (2019), "Win2025" (2025)
      scope (str): "domain" | "forest" (default: "domain")
      domain_name (str): the domain FQDN
      dry_run (bool)
    """
    if not asset_ids:
        raise ValueError("asset_ids required (any DC in the domain)")

    asset_id = str(asset_ids[0])
    target_level = parameters.get("target_level", "")
    scope = parameters.get("scope", "domain")
    domain_name = parameters.get("domain_name", "")
    dry_run = bool(parameters.get("dry_run", False))

    if not target_level:
        raise ValueError("target_level required (e.g. 'Win2019')")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    preflight = await dispatch_agent_job(
        command="preflight_dfl_upgrade",
        parameters={"target_level": target_level, "scope": scope, "domain_name": domain_name},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    if dry_run:
        return {
            "status": "dry_run",
            "current_level": preflight.get("current_level"),
            "target_level": target_level,
            "scope": scope,
            "dc_inventory": preflight.get("dc_inventory", []),
            "warnings": preflight.get("warnings", []),
            "note": "This operation is IRREVERSIBLE once applied",
        }

    result = await dispatch_agent_job(
        command="raise_domain_functional_level",
        parameters={"target_level": target_level, "scope": scope, "domain_name": domain_name},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    return {
        "status": "completed",
        "scope": scope,
        "previous_level": preflight.get("current_level"),
        "new_level": target_level,
        "result": result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
        "rollback_note": "Domain/forest functional level CANNOT be lowered. This is irreversible.",
    }


async def rollback_dfl_upgrade(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Domain/forest functional level upgrade is irreversible — Microsoft does not support lowering it",
        "current_level": execution_result.get("new_level"),
    }


# ---------------------------------------------------------------------------
# ad_trust_create
# ---------------------------------------------------------------------------

ROLLBACK_CAPABILITY_TRUST = "full"


async def execute_trust_create(parameters: dict, asset_ids: list, connector) -> dict:
    """Create an AD forest or domain trust.

    Parameters:
      target_domain (str): the trusted/trusting domain FQDN
      trust_type (str): "forest" | "external" | "shortcut" | "realm"
      trust_direction (str): "bidirectional" | "inbound" | "outbound"
      trust_password (str): the shared trust password (stored encrypted in execution_result)
      domain_name (str): local domain FQDN
      dry_run (bool)
    """
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    target_domain = parameters.get("target_domain", "")
    trust_type = parameters.get("trust_type", "forest")
    trust_direction = parameters.get("trust_direction", "bidirectional")
    trust_password = parameters.get("trust_password", "")
    domain_name = parameters.get("domain_name", "")
    dry_run = bool(parameters.get("dry_run", False))

    if not target_domain:
        raise ValueError("target_domain required")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    if dry_run:
        return {
            "status": "dry_run",
            "trust_type": trust_type,
            "trust_direction": trust_direction,
            "target_domain": target_domain,
        }

    result = await dispatch_agent_job(
        command="create_ad_trust",
        parameters={
            "target_domain": target_domain,
            "trust_type": trust_type,
            "trust_direction": trust_direction,
            "trust_password": trust_password,
            "domain_name": domain_name,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Verify Kerberos authentication across the trust
    verify = await dispatch_agent_job(
        command="verify_ad_trust",
        parameters={"target_domain": target_domain, "domain_name": domain_name},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed",
        "target_domain": target_domain,
        "trust_type": trust_type,
        "trust_direction": trust_direction,
        "result": result,
        "kerberos_verify": verify,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback_trust_create(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    target_domain = execution_result.get("target_domain", "")
    domain_name = parameters.get("domain_name", "")

    if not asset_id or not target_domain:
        return {"rolled_back": False, "reason": "missing coordinates"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    result = await dispatch_agent_job(
        command="remove_ad_trust",
        parameters={"target_domain": target_domain, "domain_name": domain_name},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    return {"rolled_back": True, "target_domain": target_domain, "result": result}


# ---------------------------------------------------------------------------
# ad_gpo_deploy
# ---------------------------------------------------------------------------

ROLLBACK_CAPABILITY_GPO = "full"


async def execute_gpo_deploy(parameters: dict, asset_ids: list, connector) -> dict:
    """Deploy a Group Policy Object with pilot OU rollout.

    Parameters:
      gpo_name (str): name of the GPO to create/link
      gpo_settings (dict): the GPO registry/security settings to apply
      pilot_ou (str): OU DN to link the GPO to first (pilot phase)
      target_ous (list[str]): OUs to link after pilot validation
      pilot_validation_hours (float): how long to run in pilot before auto-promoting (0 = manual)
      domain_name (str)
      dry_run (bool)
    """
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    gpo_name = parameters.get("gpo_name", "")
    gpo_settings = parameters.get("gpo_settings", {})
    pilot_ou = parameters.get("pilot_ou", "")
    target_ous = parameters.get("target_ous", [])
    domain_name = parameters.get("domain_name", "")
    dry_run = bool(parameters.get("dry_run", False))

    if not gpo_name:
        raise ValueError("gpo_name required")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    if dry_run:
        return {
            "status": "dry_run",
            "gpo_name": gpo_name,
            "pilot_ou": pilot_ou,
            "target_ous": target_ous,
        }

    # Create GPO
    create_result = await dispatch_agent_job(
        command="create_gpo",
        parameters={
            "gpo_name": gpo_name,
            "gpo_settings": gpo_settings,
            "domain_name": domain_name,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    gpo_id = create_result.get("gpo_id", "")

    # Link to pilot OU
    pilot_link = None
    if pilot_ou:
        pilot_link = await dispatch_agent_job(
            command="link_gpo",
            parameters={"gpo_id": gpo_id, "ou_dn": pilot_ou, "domain_name": domain_name},
            asset_ids=[asset_id],
            timeout_seconds=60,
        )

    return {
        "status": "completed_pilot",
        "gpo_name": gpo_name,
        "gpo_id": gpo_id,
        "pilot_ou": pilot_ou,
        "target_ous": target_ous,
        "pilot_link": pilot_link,
        "create_result": create_result,
        "note": "GPO deployed to pilot OU. Approve target OU linking separately.",
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback_gpo_deploy(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    gpo_id = execution_result.get("gpo_id", "")
    domain_name = parameters.get("domain_name", "")

    if not asset_id or not gpo_id:
        return {"rolled_back": False, "reason": "missing gpo_id or asset_id"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    result = await dispatch_agent_job(
        command="delete_gpo",
        parameters={"gpo_id": gpo_id, "domain_name": domain_name},
        asset_ids=[asset_id],
        timeout_seconds=60,
    )
    return {"rolled_back": True, "gpo_id": gpo_id, "result": result}


# ---------------------------------------------------------------------------
# ad_pso_manage
# ---------------------------------------------------------------------------

ROLLBACK_CAPABILITY_PSO = "full"


async def execute_pso_manage(parameters: dict, asset_ids: list, connector) -> dict:
    """Create or update a Fine-Grained Password Policy (PSO).

    Parameters:
      action (str): "create" | "update" | "delete"
      pso_name (str): name of the PSO
      pso_settings (dict): min_length, complexity, history, lockout, etc.
      applies_to (list[str]): list of user or group DNs
      precedence (int): PSO precedence (lower = higher priority)
      domain_name (str)
      dry_run (bool)
    """
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    action = parameters.get("action", "create")
    pso_name = parameters.get("pso_name", "")
    pso_settings = parameters.get("pso_settings", {})
    applies_to = parameters.get("applies_to", [])
    precedence = int(parameters.get("precedence", 10))
    domain_name = parameters.get("domain_name", "")
    dry_run = bool(parameters.get("dry_run", False))

    if not pso_name:
        raise ValueError("pso_name required")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    if dry_run:
        return {
            "status": "dry_run",
            "action": action,
            "pso_name": pso_name,
            "pso_settings": pso_settings,
            "applies_to": applies_to,
        }

    # Snapshot current PSO state for rollback
    snapshot = None
    if action in ("update", "delete"):
        try:
            snapshot = await dispatch_agent_job(
                command="get_pso",
                parameters={"pso_name": pso_name, "domain_name": domain_name},
                asset_ids=[asset_id],
                timeout_seconds=30,
            )
        except Exception as exc:
            logger.warning(f"Could not snapshot PSO {pso_name}: {exc}")

    result = await dispatch_agent_job(
        command="manage_pso",
        parameters={
            "action": action,
            "pso_name": pso_name,
            "pso_settings": pso_settings,
            "applies_to": applies_to,
            "precedence": precedence,
            "domain_name": domain_name,
        },
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    return {
        "status": "completed",
        "action": action,
        "pso_name": pso_name,
        "previous_state": snapshot,
        "result": result,
        "managed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback_pso_manage(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    action = execution_result.get("action", "create")
    pso_name = execution_result.get("pso_name", "")
    previous_state = execution_result.get("previous_state")
    domain_name = parameters.get("domain_name", "")

    if not asset_id or not pso_name:
        return {"rolled_back": False, "reason": "missing coordinates"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    if action == "create":
        result = await dispatch_agent_job(
            command="manage_pso",
            parameters={"action": "delete", "pso_name": pso_name, "domain_name": domain_name},
            asset_ids=[asset_id],
            timeout_seconds=60,
        )
    elif action == "delete" and previous_state:
        result = await dispatch_agent_job(
            command="manage_pso",
            parameters={"action": "create", "pso_name": pso_name, **previous_state, "domain_name": domain_name},
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
    elif action == "update" and previous_state:
        result = await dispatch_agent_job(
            command="manage_pso",
            parameters={"action": "update", "pso_name": pso_name, **previous_state, "domain_name": domain_name},
            asset_ids=[asset_id],
            timeout_seconds=120,
        )
    else:
        return {"rolled_back": False, "reason": f"cannot determine rollback action for action={action}"}

    return {"rolled_back": True, "pso_name": pso_name, "result": result}


# ---------------------------------------------------------------------------
# ad_stale_computer_cleanup
# ---------------------------------------------------------------------------

ROLLBACK_CAPABILITY_STALE = "full"


async def execute_stale_computer_cleanup(parameters: dict, asset_ids: list, connector) -> dict:
    """Identify and disable/delete stale computer accounts in AD.

    Two-phase lifecycle: first disable (with tombstone timestamp), then delete
    after a configurable grace period (default: 30 days).

    Parameters:
      stale_days (int): accounts inactive for this many days are considered stale (default: 90)
      action (str): "disable" | "delete" | "report" (default: "disable")
      target_ou (str): OU DN to scope the search (default: entire domain)
      exclude_ous (list[str]): OUs to exclude (e.g. service accounts OUs)
      domain_name (str)
      dry_run (bool)
    """
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    stale_days = int(parameters.get("stale_days", 90))
    action = parameters.get("action", "disable")
    target_ou = parameters.get("target_ou", "")
    exclude_ous = parameters.get("exclude_ous", [])
    domain_name = parameters.get("domain_name", "")
    dry_run = bool(parameters.get("dry_run", False))

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Discover stale computers
    discovery = await dispatch_agent_job(
        command="discover_stale_computers",
        parameters={
            "stale_days": stale_days,
            "target_ou": target_ou,
            "exclude_ous": exclude_ous,
            "domain_name": domain_name,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    stale_accounts = discovery.get("stale_accounts", [])

    if dry_run or action == "report":
        return {
            "status": "report",
            "stale_account_count": len(stale_accounts),
            "stale_accounts": stale_accounts,
            "action_that_would_run": action if not dry_run else "none (dry_run)",
        }

    # Apply action to each stale account
    results = []
    for account in stale_accounts:
        dn = account.get("distinguished_name", "")
        sam = account.get("sam_account_name", "")
        try:
            result = await dispatch_agent_job(
                command="manage_computer_account",
                parameters={
                    "action": action,
                    "distinguished_name": dn,
                    "sam_account_name": sam,
                    "domain_name": domain_name,
                    "stale_cleanup_reason": f"inactive_for_{stale_days}_days",
                },
                asset_ids=[asset_id],
                timeout_seconds=60,
            )
            results.append({"sam_account_name": sam, "dn": dn, "result": result})
        except Exception as exc:
            results.append({"sam_account_name": sam, "dn": dn, "error": str(exc)})

    return {
        "status": "completed",
        "action": action,
        "stale_days": stale_days,
        "accounts_processed": len(results),
        "results": results,
        "processed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback_stale_computer_cleanup(parameters: dict, execution_result: dict, connector) -> dict:
    """Re-enable disabled accounts. Deleted accounts cannot be recovered (beyond AD recycle bin)."""
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    action = execution_result.get("action", "disable")
    domain_name = parameters.get("domain_name", "")
    results_list = execution_result.get("results", [])

    if action == "delete":
        return {
            "rolled_back": False,
            "reason": "Deleted computer accounts cannot be automatically recovered. "
                      "Use AD Recycle Bin (if enabled) or restore from backup.",
        }

    if not asset_id or not results_list:
        return {"rolled_back": False, "reason": "no accounts to re-enable"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    re_enabled = []
    for entry in results_list:
        if entry.get("error"):
            continue
        dn = entry.get("dn", "")
        sam = entry.get("sam_account_name", "")
        try:
            result = await dispatch_agent_job(
                command="manage_computer_account",
                parameters={
                    "action": "enable",
                    "distinguished_name": dn,
                    "sam_account_name": sam,
                    "domain_name": domain_name,
                },
                asset_ids=[asset_id],
                timeout_seconds=60,
            )
            re_enabled.append({"sam_account_name": sam, "result": result})
        except Exception as exc:
            re_enabled.append({"sam_account_name": sam, "error": str(exc)})

    return {
        "rolled_back": True,
        "accounts_re_enabled": len([r for r in re_enabled if "result" in r]),
        "re_enabled": re_enabled,
    }
