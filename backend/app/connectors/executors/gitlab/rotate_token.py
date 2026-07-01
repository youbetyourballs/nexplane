# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_gitlab_client, GitLabClient


async def execute(parameters, asset_ids, connector):
    username = parameters.get("username") or parameters.get("user_identifier", "")
    token_name = parameters.get("token_name", f"nexplane-rotated-{int(datetime.now(timezone.utc).timestamp())}")
    scopes = parameters.get("scopes") or ["api"]
    client = get_gitlab_client(connector)
    if not client and parameters.get("gitlab_url"):
        client = GitLabClient(url=parameters["gitlab_url"],
                              token=parameters.get("gitlab_token", ""))
    if not client:
        return {"action": "gitlab_rotate_token", "status": "skipped",
                "reason": "no_gitlab_credentials", "username": username}
    user = await client.get_user_by_username(username)
    if not user:
        return {"action": "gitlab_rotate_token", "status": "error",
                "reason": f"user_not_found: {username}"}
    user_id = user["id"]
    # Revoke all active tokens for this user
    old_tokens = await client.list_personal_access_tokens(user_id)
    revoked_ids = []
    for tok in old_tokens:
        try:
            await client.revoke_personal_access_token(tok["id"])
            revoked_ids.append(tok["id"])
        except Exception:
            pass
    # Create replacement token
    new_token = await client.create_personal_access_token(user_id, token_name, scopes)
    return {
        "action": "gitlab_rotate_token",
        "username": username,
        "user_id": user_id,
        "revoked_token_ids": revoked_ids,
        "new_token_id": new_token.get("id"),
        "new_token_name": new_token.get("name"),
        "new_token_value": new_token.get("token"),
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters, execution_result, connector):
    # Rollback: revoke the newly created token (cannot restore old ones)
    new_token_id = execution_result.get("new_token_id")
    client = get_gitlab_client(connector)
    if not client and parameters.get("gitlab_url"):
        client = GitLabClient(url=parameters["gitlab_url"],
                              token=parameters.get("gitlab_token", ""))
    if not client:
        return {"rolled_back": False, "reason": "no_gitlab_credentials"}
    if not new_token_id:
        return {"rolled_back": False, "reason": "no_new_token_id_in_execution_result"}
    try:
        await client.revoke_personal_access_token(new_token_id)
        return {"rolled_back": True, "revoked_token_id": new_token_id}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
