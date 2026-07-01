# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_iam_users",
        "assets": [
            {"id": "arn:aws:iam::123456789:user/alice", "name": "alice", "asset_type": "identity", "metadata": {"mfa_enabled": True, "password_last_used": "2026-04-01"}},
            {"id": "arn:aws:iam::123456789:user/svc-deploy", "name": "svc-deploy", "asset_type": "identity", "metadata": {"mfa_enabled": False, "password_last_used": None}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_execute(creds: dict) -> dict:
    from ._client import get_iam_client
    iam = get_iam_client(creds)
    loop = asyncio.get_event_loop()

    def _call():
        paginator = iam.get_paginator('list_users')
        assets = []
        for page in paginator.paginate():
            for user in page['Users']:
                mfa = iam.list_mfa_devices(UserName=user['UserName'])
                assets.append({
                    "id": user['Arn'],
                    "name": user['UserName'],
                    "asset_type": "identity",
                    "metadata": {
                        "mfa_enabled": len(mfa.get('MFADevices', [])) > 0,
                        "password_last_used": user.get('PasswordLastUsed', {}).isoformat() if hasattr(user.get('PasswordLastUsed'), 'isoformat') else None,
                        "create_date": user['CreateDate'].isoformat(),
                    },
                })
        return assets

    assets = await loop.run_in_executor(None, _call)
    return {"action": "discover_iam_users", "assets": assets, "discovered_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
