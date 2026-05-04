from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, 'credentials', {})
    expiry_seconds = parameters.get('expiry_seconds', 3600)
    reusable = parameters.get('reusable', False)
    ephemeral = parameters.get('ephemeral', True)
    tags = parameters.get('tags', [])

    if not creds:
        return {
            "action": "generate_auth_key",
            "auth_key": "tskey-auth-mock000000000000000000",
            "expires_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    # If a pre-generated reusable auth key is stored in credentials, use it directly.
    # This is the simplest path — generate a reusable key in tailscale.com/admin/settings/keys
    # and store it as auth_key in the connector credentials.
    if creds.get('auth_key'):
        return {
            "action": "generate_auth_key",
            "auth_key": creds['auth_key'],
            "source": "stored",
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import ts_post
    tailnet = creds.get('tailnet') or '-'
    # Tailscale OAuth-generated keys must have at least one tag defined in the ACL.
    # Default to tag:nexplane — add "tag:nexplane": ["autogroup:admin"] to your ACL policy.
    resolved_tags = [f"tag:{t}" for t in tags] if tags else ["tag:nexplane"]
    body = {
        "capabilities": {
            "devices": {
                "create": {
                    "reusable": reusable,
                    "ephemeral": ephemeral,
                    "preauthorized": True,
                    "tags": resolved_tags,
                }
            }
        },
        "expirySeconds": expiry_seconds,
    }
    resp = await ts_post(f"/tailnet/{tailnet}/keys", body, creds)
    return {
        "action": "generate_auth_key",
        "auth_key": resp.get("key", ""),
        "id": resp.get("id", ""),
        "expires_at": resp.get("expires", ""),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "auth keys expire automatically"}
