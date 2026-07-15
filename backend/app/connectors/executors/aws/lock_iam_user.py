# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_boto3_client

_LOCKOUT_POLICY_NAME = "nexplane-emergency-lockout"
_LOCKOUT_POLICY_DOC = """{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Deny",
    "Action": "*",
    "Resource": "*"
  }]
}"""


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    username = parameters.get("username") or parameters.get("user_name") or parameters.get("user_identifier", "")

    if not creds:
        return {
            "locked": True,
            "username": username,
            "mock": True,
            "pre_state": {},
        }

    loop = asyncio.get_event_loop()
    iam = get_boto3_client(creds, "iam")

    # 1. Capture pre-state: list all access keys and their statuses
    def _list_keys():
        resp = iam.list_access_keys(UserName=username)
        return [
            {"AccessKeyId": k["AccessKeyId"], "Status": k["Status"]}
            for k in resp.get("AccessKeyMetadata", [])
        ]

    access_keys = await loop.run_in_executor(None, _list_keys)
    pre_state = {"access_keys": access_keys}

    # 2. Attach deny-all lockout policy
    def _lock():
        iam.put_user_policy(
            UserName=username,
            PolicyName=_LOCKOUT_POLICY_NAME,
            PolicyDocument=_LOCKOUT_POLICY_DOC,
        )

    await loop.run_in_executor(None, _lock)
    return {
        "locked": True,
        "username": username,
        "policy_name": _LOCKOUT_POLICY_NAME,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})
    username = execution_result.get("username") or parameters.get("username") or parameters.get("user_name") or parameters.get("user_identifier", "")

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    loop = asyncio.get_event_loop()
    iam = get_boto3_client(creds, "iam")

    try:
        # Remove the lockout policy
        def _unlock():
            iam.delete_user_policy(
                UserName=username,
                PolicyName=_LOCKOUT_POLICY_NAME,
            )

        await loop.run_in_executor(None, _unlock)

        # Restore any access keys that were Active before lockout
        access_keys = pre_state.get("access_keys", [])
        restored_keys = []
        for key in access_keys:
            if key["Status"] == "Active":
                def _activate(key_id=key["AccessKeyId"]):
                    iam.update_access_key(
                        UserName=username,
                        AccessKeyId=key_id,
                        Status="Active",
                    )
                await loop.run_in_executor(None, _activate)
                restored_keys.append(key["AccessKeyId"])

        return {
            "rolled_back": True,
            "username": username,
            "policy_removed": _LOCKOUT_POLICY_NAME,
            "keys_restored_to_active": restored_keys,
        }
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
