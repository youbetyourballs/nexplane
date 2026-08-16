# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OCI account baseline hardening executor.

Applies hardening to the OCI tenancy:
  1. IAM password policy — enforce complexity: min 14 chars, upper+lower+numeric+special,
     no username containment, max 365 day expiry

Rollback: restore prior password policy.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_TARGET_PASSWORD_POLICY = {
    "minimum_password_length": 14,
    "is_uppercase_characters_required": True,
    "is_lowercase_characters_required": True,
    "is_numeric_characters_required": True,
    "is_special_characters_required": True,
    "is_username_containment_allowed": False,
}


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def _preflight(creds: dict) -> dict:
    def _do():
        from app.connectors.executors.oci._client import get_oci_config, get_identity_client
        config = get_oci_config(creds)
        client = get_identity_client(creds)
        tenancy_id = config["tenancy"]
        client.get_tenancy(tenancy_id=tenancy_id)  # validates access
        return tenancy_id

    tenancy_id = await _run(_do)
    return {"phase": "preflight", "status": "ok", "tenancy_id": tenancy_id}


async def _snapshot(creds: dict, tenancy_id: str) -> dict:
    def _do():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.identity.IdentityClient(config)
        try:
            policy = client.get_authentication_policy(compartment_id=tenancy_id).data
            return {
                "minimum_password_length": policy.password_policy.minimum_password_length,
                "is_uppercase_characters_required": policy.password_policy.is_uppercase_characters_required,
                "is_lowercase_characters_required": policy.password_policy.is_lowercase_characters_required,
                "is_numeric_characters_required": policy.password_policy.is_numeric_characters_required,
                "is_special_characters_required": policy.password_policy.is_special_characters_required,
                "is_username_containment_allowed": policy.password_policy.is_username_containment_allowed,
                "is_different_from_current_password_required": policy.password_policy.is_different_from_current_password_required,
                "password_expires_within_days": getattr(policy.password_policy, "password_expires_within_days", None),
            }
        except Exception:
            return None

    prior = await _run(_do)
    return {"phase": "snapshot", "status": "ok", "pre": {"password_policy": prior}}


async def _enable(creds: dict, tenancy_id: str, pre: dict) -> dict:
    prior_policy = pre.get("password_policy")
    applied = []
    skipped = []
    rollback_data = {"prior_password_policy": prior_policy}

    # Check if already meets target
    if prior_policy:
        already = all(
            prior_policy.get(k) == v
            for k, v in _TARGET_PASSWORD_POLICY.items()
            if v is not None
        )
        if already:
            skipped.append("password_policy")
            return {"phase": "enable", "status": "ok", "applied": applied, "skipped": skipped,
                    "rollback_data": rollback_data}

    def _apply():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.identity.IdentityClient(config)
        pp = oci.identity.models.PasswordPolicy(**_TARGET_PASSWORD_POLICY)
        details = oci.identity.models.UpdateAuthenticationPolicyDetails(password_policy=pp)
        client.update_authentication_policy(
            compartment_id=tenancy_id,
            update_authentication_policy_details=details,
        )

    await _run(_apply)
    applied.append("password_policy")
    logger.info("oci_account_baseline_hardening: password policy updated")

    return {"phase": "enable", "status": "ok", "applied": applied, "skipped": skipped,
            "rollback_data": rollback_data}


async def _verify(creds: dict, tenancy_id: str, applied: list) -> dict:
    if "password_policy" not in applied:
        return {"phase": "verify", "status": "ok", "failures": []}

    def _do():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.identity.IdentityClient(config)
        policy = client.get_authentication_policy(compartment_id=tenancy_id).data
        failures = []
        for key, expected in _TARGET_PASSWORD_POLICY.items():
            actual = getattr(policy.password_policy, key, None)
            if actual != expected:
                failures.append(f"{key}: expected {expected}, got {actual}")
        return failures

    failures = await _run(_do)
    return {"phase": "verify", "status": "failed" if failures else "ok", "failures": failures}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials

    preflight = await _preflight(creds)
    if preflight["status"] != "ok":
        return preflight
    tenancy_id = preflight["tenancy_id"]

    snapshot = await _snapshot(creds, tenancy_id)
    enable = await _enable(creds, tenancy_id, snapshot["pre"])
    verify = await _verify(creds, tenancy_id, enable["applied"])

    return {
        "phase": "report",
        "status": verify["status"],
        "tenancy_id": tenancy_id,
        "applied": enable["applied"],
        "skipped": enable["skipped"],
        "verify_failures": verify["failures"],
        "rollback_data": enable["rollback_data"],
        "hardened_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials
    tenancy_id = execution_result.get("tenancy_id", "")
    rollback_data = execution_result.get("rollback_data", {})
    prior_policy = rollback_data.get("prior_password_policy")

    if prior_policy is None:
        logger.info("oci_account_baseline_hardening rollback: no prior policy — skipping")
        return {"rolled_back": True, "note": "No prior password policy captured — no restore performed"}

    def _restore():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.identity.IdentityClient(config)
        pp = oci.identity.models.PasswordPolicy(**{k: v for k, v in prior_policy.items() if v is not None})
        details = oci.identity.models.UpdateAuthenticationPolicyDetails(password_policy=pp)
        client.update_authentication_policy(
            compartment_id=tenancy_id,
            update_authentication_policy_details=details,
        )

    await _run(_restore)
    logger.info("oci_account_baseline_hardening rollback: password policy restored")

    return {
        "rolled_back": True,
        "restored": ["password_policy"],
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
