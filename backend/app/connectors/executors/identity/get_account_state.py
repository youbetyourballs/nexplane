# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Read pre-state from raw_attributes stored in IdentityAccount.
Called before any fan-out child CR executes its action.
"""
from __future__ import annotations

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

# Fields captured per connector type. Keys must be stable — used by restore.
_STATE_FIELDS: dict[str, list[str]] = {
    "active_directory": ["enabled", "locked", "group_memberships", "mfa_enforced"],
    "okta":             ["status", "mfa_enrolled_factors", "app_assignments"],
    "entra_id":         ["accountEnabled", "signInSessionsValidFromDateTime", "assigned_roles"],
    "github":           ["org_membership_state", "role", "team_memberships"],
    "gitlab":           ["state", "group_memberships"],
    "ldap":             ["enabled", "locked"],
    "freeipa":          ["enabled", "locked"],
    "keycloak":         ["enabled", "required_actions"],
    "gitea":            ["login", "active", "prohibited_login"],
    "teleport":         ["locked", "lock_expires"],
    "kubernetes":       ["rolebindings"],
}


def build_pre_state_from_raw(connector_type: str, raw: dict) -> dict:
    """Extract the fields we care about from raw_attributes for pre-state snapshot."""
    fields = _STATE_FIELDS.get(connector_type, [])
    return {k: raw[k] for k in fields if k in raw}


async def get_account_state(connector, external_id: str, connector_type: str) -> dict:
    """
    Live read of current account state from the connector.
    Falls back to empty dict if the connector doesn't support it.
    """
    try:
        from app.services.connector_service import execute_action
        result = await execute_action(connector, "get_user", {"user_id": external_id})
        raw = result.get("user") or result.get("account") or result or {}
        return build_pre_state_from_raw(connector_type, raw)
    except Exception:
        return {}
