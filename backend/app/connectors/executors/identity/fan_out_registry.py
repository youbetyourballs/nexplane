# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

FAN_OUT_ACTIONS: dict[str, dict[str, str]] = {
    "emergency_user_lockout": {
        "active_directory": "disable_account",
        "okta":             "suspend_user",
        "entra_id":         "disable_user",
        "github":           "suspend_org_member",
        "gitlab":           "gitlab_suspend_user",
        "ldap":             "ldap_disable_user",
        "freeipa":          "freeipa_disable_user",
        "keycloak":         "keycloak_disable_user",
        "gitea":            "gitea_suspend_user",
        "teleport":         "teleport_lock_user",
        "kubernetes":       "k8s_revoke_rolebinding",
    },
    "user_suspension": {
        "active_directory": "disable_account",
        "okta":             "suspend_user",
        "entra_id":         "disable_user",
        "github":           "suspend_org_member",
        "gitlab":           "gitlab_suspend_user",
        "ldap":             "ldap_disable_user",
        "freeipa":          "freeipa_disable_user",
        "keycloak":         "keycloak_disable_user",
        "gitea":            "gitea_suspend_user",
    },
    "enforce_mfa": {
        "active_directory": "enforce_mfa",
        "okta":             "enforce_mfa",
        "entra_id":         "reset_mfa",
    },
    "user_scope_reduction": {
        "active_directory": "remove_from_group",
        "okta":             "deprovision_from_app",
        "entra_id":         "remove_from_role",
        "github":           "remove_org_member",
        "kubernetes":       "k8s_revoke_rolebinding",
    },
}

FAN_OUT_CHANGE_TYPES: frozenset[str] = frozenset(FAN_OUT_ACTIONS.keys())


def get_fan_out_action(change_type: str, connector_type: str) -> str | None:
    return FAN_OUT_ACTIONS.get(change_type, {}).get(connector_type)


def is_fan_out_change_type(change_type: str) -> bool:
    return change_type in FAN_OUT_CHANGE_TYPES
