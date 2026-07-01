# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Restore account state from pre_state snapshot.
Called by child CR rollback.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

RESTORABLE_CONNECTOR_TYPES = frozenset({
    "active_directory", "okta", "entra_id", "github", "gitlab",
    "ldap", "freeipa", "keycloak", "gitea", "teleport", "kubernetes",
})


def compute_restore_ops(connector_type: str, pre_state: dict, post_state: dict) -> list[dict]:
    """
    Diff pre vs post state and return a list of restore operations to apply.
    Each op: {"action": str, "params": dict}
    """
    ops: list[dict] = []

    if connector_type in ("active_directory", "ldap", "freeipa", "keycloak", "gitea"):
        pre_enabled = pre_state.get("enabled", True)
        post_enabled = post_state.get("enabled", True)
        if pre_enabled and not post_enabled:
            ops.append({"action": "enable_account", "params": {}})
        elif not pre_enabled and post_enabled:
            ops.append({"action": "disable_account", "params": {}})

    elif connector_type == "okta":
        pre_status = pre_state.get("status", "ACTIVE")
        post_status = post_state.get("status", "ACTIVE")
        if pre_status != post_status:
            if pre_status == "ACTIVE":
                ops.append({"action": "unsuspend_user", "params": {}})
            elif pre_status == "SUSPENDED":
                ops.append({"action": "suspend_user", "params": {}})

    elif connector_type == "entra_id":
        pre_enabled = pre_state.get("accountEnabled", True)
        post_enabled = post_state.get("accountEnabled", True)
        if pre_enabled and not post_enabled:
            ops.append({"action": "enable_user", "params": {}})
        elif not pre_enabled and post_enabled:
            ops.append({"action": "disable_user", "params": {}})

    elif connector_type == "github":
        pre_state_val = pre_state.get("org_membership_state", "active")
        post_state_val = post_state.get("org_membership_state", "active")
        if pre_state_val == "active" and post_state_val != "active":
            ops.append({"action": "unsuspend_org_member", "params": {}})

    elif connector_type == "gitlab":
        pre_state_val = pre_state.get("state", "active")
        post_state_val = post_state.get("state", "active")
        if pre_state_val == "active" and post_state_val == "blocked":
            ops.append({"action": "gitlab_unblock_user", "params": {}})

    elif connector_type == "teleport":
        pre_locked = pre_state.get("locked", False)
        post_locked = post_state.get("locked", False)
        if not pre_locked and post_locked:
            ops.append({"action": "teleport_unlock_user", "params": {}})

    elif connector_type == "kubernetes":
        pre_rbs = pre_state.get("rolebindings", [])
        post_rbs = post_state.get("rolebindings", [])
        removed = [rb for rb in pre_rbs if rb not in post_rbs]
        for rb in removed:
            ops.append({"action": "k8s_restore_rolebinding", "params": {"rolebinding": rb}})

    return ops


async def restore_account_state(
    connector,
    external_id: str,
    connector_type: str,
    pre_state: dict,
) -> dict:
    """Restore an account to its pre-state snapshot."""
    if connector_type not in RESTORABLE_CONNECTOR_TYPES:
        return {"status": "skipped", "reason": f"no restore logic for {connector_type}"}

    if not pre_state:
        return {"status": "skipped", "reason": "no pre_state captured"}

    try:
        from app.services.connector_service import execute_action

        current_raw = {}
        try:
            result = await execute_action(connector, "get_user", {"user_id": external_id})
            current_raw = result.get("user") or result.get("account") or {}
        except Exception:
            pass

        from app.connectors.executors.identity.get_account_state import build_pre_state_from_raw
        current_state = build_pre_state_from_raw(connector_type, current_raw)
        ops = compute_restore_ops(connector_type, pre_state, current_state)

        warnings = []
        for op in ops:
            try:
                await execute_action(connector, op["action"], {"user_id": external_id, **op["params"]})
            except Exception as exc:
                warnings.append(f"{op['action']}: {exc}")

        return {
            "status": "completed" if not warnings else "partial",
            "ops_applied": len(ops) - len(warnings),
            "warnings": warnings,
        }
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
