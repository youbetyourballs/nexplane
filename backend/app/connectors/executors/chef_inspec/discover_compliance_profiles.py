# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_compliance_profiles", "profiles": [{"name": "cis-aws", "version": "1.0.0"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/compliance/profiles/search", params={"page": 1, "per_page": 100})
        resp.raise_for_status()
        profiles = [{"name": p.get("name"), "version": p.get("version"), "title": p.get("title")} for p in resp.json().get("profiles", [])]
    return {"action": "discover_compliance_profiles", "profiles": profiles, "count": len(profiles)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
