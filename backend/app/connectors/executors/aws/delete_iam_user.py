# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    username = parameters.get('username', '')

    if not creds:
        return {"action": "delete_iam_user", "username": username, "deleted": True, "mock": True}

    from ._client import get_boto3_client
    iam = get_boto3_client(creds, 'iam')
    loop = asyncio.get_event_loop()

    def _capture():
        user = iam.get_user(UserName=username)["User"]
        attached = iam.list_attached_user_policies(UserName=username)["AttachedPolicies"]
        inline_names = iam.list_user_policies(UserName=username)["PolicyNames"]
        inline_docs = {}
        for p in inline_names:
            inline_docs[p] = iam.get_user_policy(UserName=username, PolicyName=p)["PolicyDocument"]
        groups = iam.list_groups_for_user(UserName=username)["Groups"]
        tags = iam.list_user_tags(UserName=username)["Tags"]
        return {
            "username": username,
            "path": user.get("Path", "/"),
            "attached_policies": [{"PolicyArn": p["PolicyArn"], "PolicyName": p["PolicyName"]} for p in attached],
            "inline_policies": inline_docs,
            "groups": [g["GroupName"] for g in groups],
            "tags": tags,
        }

    def _delete():
        # Delete all access keys
        keys = iam.list_access_keys(UserName=username)['AccessKeyMetadata']
        for k in keys:
            iam.delete_access_key(UserName=username, AccessKeyId=k['AccessKeyId'])
        # Detach all managed policies
        policies = iam.list_attached_user_policies(UserName=username)['AttachedPolicies']
        for p in policies:
            iam.detach_user_policy(UserName=username, PolicyArn=p['PolicyArn'])
        # Delete inline policies (delete_user fails if any remain)
        inline = iam.list_user_policies(UserName=username)['PolicyNames']
        for name in inline:
            iam.delete_user_policy(UserName=username, PolicyName=name)
        # Delete login profile if exists
        try:
            iam.delete_login_profile(UserName=username)
        except iam.exceptions.NoSuchEntityException:
            pass
        iam.delete_user(UserName=username)

    pre_state = await loop.run_in_executor(None, _capture)

    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
            pre_state,
        )
        await db.commit()

    await loop.run_in_executor(None, _delete)

    return {
        "action": "delete_iam_user",
        "username": username,
        "deleted": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        pre_state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(parameters["cr_id"]),
            parameters.get("step_id", "step_0"),
            _uuid.UUID(parameters["org_id"]),
        )
    if not pre_state:
        return {"rolled_back": False, "reason": "no pre-state captured — cannot reconstitute IAM user"}

    creds = getattr(connector, 'credentials', {})
    from ._client import get_boto3_client
    iam = get_boto3_client(creds, 'iam')
    loop = asyncio.get_event_loop()

    def _recreate():
        username = pre_state["username"]
        iam.create_user(UserName=username, Path=pre_state.get("path", "/"))
        for policy in pre_state.get("attached_policies", []):
            iam.attach_user_policy(UserName=username, PolicyArn=policy["PolicyArn"])
        for policy_name, doc in pre_state.get("inline_policies", {}).items():
            import json
            iam.put_user_policy(UserName=username, PolicyName=policy_name, PolicyDocument=json.dumps(doc))
        for group in pre_state.get("groups", []):
            try:
                iam.add_user_to_group(UserName=username, GroupName=group)
            except Exception:
                pass  # group may have been deleted
        if pre_state.get("tags"):
            iam.tag_user(UserName=username, Tags=pre_state["tags"])
        return username

    username = await loop.run_in_executor(None, _recreate)
    return {
        "rolled_back": True,
        "username": username,
        "note": "IAM user recreated with original policies, groups, and tags. Access keys cannot be restored.",
    }
