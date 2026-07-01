# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def get_hosts_api(creds: dict):
    from falconpy import Hosts
    return Hosts(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        base_url=creds.get("base_url", "https://api.crowdstrike.com"),
    )


def get_device_control_api(creds: dict):
    from falconpy import DeviceControlPolicies
    return DeviceControlPolicies(
        client_id=creds["client_id"],
        client_secret=creds["client_secret"],
        base_url=creds.get("base_url", "https://api.crowdstrike.com"),
    )


async def get_token(creds: dict) -> str:
    import httpx
    base_url = creds.get('base_url', 'https://api.crowdstrike.com')
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base_url}/oauth2/token", data={"client_id": creds["client_id"], "client_secret": creds["client_secret"]})
        resp.raise_for_status()
        return resp.json()["access_token"]
