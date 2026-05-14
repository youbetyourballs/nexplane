from datetime import datetime, timezone

import httpx

TAILSCALE_API_BASE = "https://api.tailscale.com/api/v2"


def _tailscale_auth(creds: dict) -> tuple[str, dict] | tuple[None, None]:
    """Return (tailnet, headers) or (None, None) if no credentials."""
    api_key = creds.get("api_key") or creds.get("token")
    tailnet = creds.get("tailnet") or creds.get("org") or "-"
    if not api_key:
        return None, None
    import base64
    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return tailnet, {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _mock_response():
    return {
        "action": "list_devices",
        "devices": [
            {
                "id": "mock-device-1",
                "name": "laptop.example.ts.net",
                "addresses": ["100.64.0.1"],
                "user": "alice@example.com",
                "os": "linux",
                "authorized": True,
            },
            {
                "id": "mock-device-2",
                "name": "server.example.ts.net",
                "addresses": ["100.64.0.2"],
                "user": "bob@example.com",
                "os": "linux",
                "authorized": True,
            },
        ],
        "mock": True,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", None) or {}
    tailnet, headers = _tailscale_auth(creds)
    if not tailnet:
        return _mock_response()

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{TAILSCALE_API_BASE}/tailnet/{tailnet}/devices",
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()

    devices = [
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "addresses": d.get("addresses", []),
            "user": d.get("user"),
            "os": d.get("os"),
            "authorized": d.get("authorized"),
            "last_seen": d.get("lastSeen"),
        }
        for d in data.get("devices", [])
    ]
    return {
        "action": "list_devices",
        "devices": devices,
        "count": len(devices),
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "list actions have no rollback"}
