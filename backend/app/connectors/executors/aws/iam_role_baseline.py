# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""AWS IAM Role Baseline executor.

Creates three standard security IAM roles in the target AWS account:
  - NexplaneReadOnly         (SecurityAudit + ReadOnlyAccess)
  - NexplaneSecurityAudit    (SecurityAudit)
  - NexplaneBreakGlass       (AdministratorAccess, MFA required)

Idempotent — skips roles that already exist.
Rollback deletes only net-new roles (those that did not exist before execute).
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

MANAGED_POLICIES = {
    "ReadOnlyAccess": "arn:aws:iam::aws:policy/ReadOnlyAccess",
    "SecurityAudit": "arn:aws:iam::aws:policy/SecurityAudit",
    "AdministratorAccess": "arn:aws:iam::aws:policy/AdministratorAccess",
}

COMMON_TAGS = [
    {"Key": "ManagedBy", "Value": "nexplane"},
    {"Key": "Purpose", "Value": "security-baseline"},
]


def _c(connector, service, region="us-east-1"):
    from app.connectors.executors.aws.reference_scan import _boto_client
    return _boto_client(service, connector, region)


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _trust_policy(account_id: str, require_mfa: bool = False) -> str:
    statement = {
        "Effect": "Allow",
        "Principal": {"AWS": f"arn:aws:iam::{account_id}:root"},
        "Action": "sts:AssumeRole",
    }
    if require_mfa:
        statement["Condition"] = {"Bool": {"aws:MultiFactorAuthPresent": "true"}}
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [statement],
    })


def _role_definitions(account_id: str, prefix: str = "Nexplane") -> list:
    return [
        {
            "name": f"{prefix}ReadOnly",
            "trust_policy": _trust_policy(account_id, require_mfa=False),
            "policies": [MANAGED_POLICIES["SecurityAudit"], MANAGED_POLICIES["ReadOnlyAccess"]],
            "extra_tags": [],
        },
        {
            "name": f"{prefix}SecurityAudit",
            "trust_policy": _trust_policy(account_id, require_mfa=False),
            "policies": [MANAGED_POLICIES["SecurityAudit"]],
            "extra_tags": [],
        },
        {
            "name": f"{prefix}BreakGlass",
            "trust_policy": _trust_policy(account_id, require_mfa=True),
            "policies": [MANAGED_POLICIES["AdministratorAccess"]],
            "extra_tags": [{"Key": "BreakGlass", "Value": "true"}],
        },
    ]


async def _execute_real(connector, parameters: dict) -> dict:
    prefix = parameters.get("role_name_prefix", "Nexplane")
    skip_if_exists = parameters.get("skip_if_exists", True)

    def _get_account_id():
        sts = _c(connector, "sts")
        return sts.get_caller_identity()["Account"]

    account_id = await _run(_get_account_id)
    role_defs = _role_definitions(account_id, prefix)

    created_roles = []
    skipped_roles = []
    pre_state_roles = []

    for role_def in role_defs:
        role_name = role_def["name"]

        def _check_exists(rn=role_name):
            iam = _c(connector, "iam")
            try:
                iam.get_role(RoleName=rn)
                return True
            except iam.exceptions.NoSuchEntityException:
                return False
            except Exception as e:
                # Treat ClientError with NoSuchEntity code as not existing
                err_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
                if err_code == "NoSuchEntity":
                    return False
                raise

        exists = await _run(_check_exists)
        pre_state_roles.append({"name": role_name, "was_new": not exists})

        if exists and skip_if_exists:
            skipped_roles.append(role_name)
            continue

        tags = COMMON_TAGS + role_def.get("extra_tags", [])

        def _create(rn=role_name, tp=role_def["trust_policy"], t=tags, policies=role_def["policies"]):
            iam = _c(connector, "iam")
            iam.create_role(
                RoleName=rn,
                AssumeRolePolicyDocument=tp,
                Tags=t,
            )
            for policy_arn in policies:
                iam.attach_role_policy(RoleName=rn, PolicyArn=policy_arn)

        await _run(_create)
        created_roles.append(role_name)

    return {
        "created_roles": created_roles,
        "skipped_roles": skipped_roles,
        "account_id": account_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "pre_state": {"roles": pre_state_roles},
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        prefix = parameters.get("role_name_prefix", "Nexplane")
        mock_roles = [f"{prefix}ReadOnly", f"{prefix}SecurityAudit", f"{prefix}BreakGlass"]
        return {
            "mock": True,
            "created_roles": mock_roles,
            "skipped_roles": [],
            "account_id": "123456789012",
            "pre_state": {"roles": [{"name": r, "was_new": True} for r in mock_roles]},
        }

    try:
        return await _execute_real(connector, parameters)
    except Exception as e:
        logger.error("aws_iam_role_baseline execute failed: %s", e)
        raise


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": True, "mock": True, "deleted_roles": []}

    pre_state = execution_result.get("pre_state", {})
    roles = pre_state.get("roles", [])

    # Only delete roles that were net-new (did not exist before execute)
    to_delete = [r["name"] for r in roles if r.get("was_new", False)]

    deleted_roles = []
    failed_roles = []

    for role_name in reversed(to_delete):
        def _delete(rn=role_name):
            iam = _c(connector, "iam")
            # Detach all managed policies first
            try:
                paginator = iam.get_paginator("list_attached_role_policies")
                for page in paginator.paginate(RoleName=rn):
                    for policy in page["AttachedPolicies"]:
                        iam.detach_role_policy(RoleName=rn, PolicyArn=policy["PolicyArn"])
            except Exception:
                pass
            iam.delete_role(RoleName=rn)

        try:
            await _run(_delete)
            deleted_roles.append(role_name)
        except Exception as e:
            logger.error("Rollback: failed to delete role %s: %s", role_name, e)
            failed_roles.append({"role": role_name, "error": str(e)})

    all_ok = len(failed_roles) == 0
    return {
        "rolled_back": all_ok,
        "deleted_roles": deleted_roles,
        "failed_roles": failed_roles,
    }
