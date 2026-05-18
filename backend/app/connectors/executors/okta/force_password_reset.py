from datetime import datetime, timezone
import httpx
from ._client import okta_headers, okta_base


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]

    if not creds:
        return {
            "action": "force_password_reset",
            "user_id": user_id,
            "simulated": True,
            "reset_at": datetime.now(timezone.utc).isoformat(),
        }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{okta_base(creds)}/users/{user_id}/lifecycle/reset_password?sendEmail=false",
            headers=okta_headers(creds),
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "action": "force_password_reset",
        "user_id": user_id,
        "reset_url": data.get("resetPasswordUrl"),
        "reset_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "password reset has no rollback — the reset link expires naturally",
    }
