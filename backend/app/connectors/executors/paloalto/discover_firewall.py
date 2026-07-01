# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


def _mock_response():
    return {
        "action": "discover_firewall",
        "assets": [
            {"id": "palo-fw-001", "name": "PA-820-prod", "asset_type": "firewall", "metadata": {"model": "PA-820", "os_version": "10.2.4", "ha_enabled": False}},
        ],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    if not creds:
        return _mock_response()
    # PAN-OS XML API
    import asyncio
    import httpx
    import urllib.parse
    hostname = creds['hostname']
    username = creds['username']
    password = creds['password']
    async with httpx.AsyncClient(verify=False) as client:
        # Get API key
        key_resp = await client.get(f"https://{hostname}/api/?type=keygen&user={urllib.parse.quote(username)}&password={urllib.parse.quote(password)}")
        key_resp.raise_for_status()
        import xml.etree.ElementTree as ET
        key = ET.fromstring(key_resp.text).findtext('.//key')
        # Get system info
        sys_resp = await client.get(f"https://{hostname}/api/?type=op&cmd=<show><system><info></info></system></show>&key={key}")
        root = ET.fromstring(sys_resp.text)
        model = root.findtext('.//model') or 'unknown'
        os_version = root.findtext('.//sw-version') or 'unknown'
        serial = root.findtext('.//serial') or hostname
    return {
        "action": "discover_firewall",
        "assets": [{"id": serial, "name": f"PAN-{serial}", "asset_type": "firewall", "metadata": {"model": model, "os_version": os_version, "hostname": hostname}}],
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover actions have no rollback"}
