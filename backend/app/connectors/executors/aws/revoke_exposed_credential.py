"""Executor: immediately revoke a known-compromised credential. NO rollback — permanent."""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    credential_type = parameters["credential_type"]
    credential_id = parameters["credential_id"]
    creds = getattr(connector, "creds", None)

    if not creds:
        logger.info("[mock] Would revoke %s %s", credential_type, credential_id)
        return {"success": True, "mock": True, "rolled_back_available": False}

    if credential_type == "aws_iam_key":
        import boto3
        iam = boto3.client(
            "iam",
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
            region_name=creds.get("region", "us-east-1"),
        )
        iam.delete_access_key(AccessKeyId=credential_id)
        logger.info("Revoked IAM key %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    if credential_type == "vault_token":
        import httpx
        vault_addr = creds.get("vault_addr", "http://localhost:8200")
        vault_token = creds.get("vault_token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{vault_addr}/v1/auth/token/revoke",
                headers={"X-Vault-Token": vault_token},
                json={"token": credential_id},
            )
            resp.raise_for_status()
        return {"success": True, "rolled_back_available": False}

    raise ValueError(f"Unsupported credential_type: {credential_type}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    return {"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}
