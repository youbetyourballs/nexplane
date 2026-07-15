# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: immediately revoke a known-compromised credential.
For aws_iam_key: reconstitution rollback — saves username before delete, creates new key on rollback.
Other credential types: permanent, no rollback.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def _reconstitute(rp: dict, connector: Any) -> dict:
    """Called during rollback — create a new key for the same user."""
    if rp.get("credential_type") != "aws_iam_key":
        return {"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials available for reconstitution"}
    import boto3
    iam = boto3.client(
        "iam",
        aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
        region_name=creds.get("region", "us-east-1"),
    )
    new_key = iam.create_access_key(UserName=rp["username"])["AccessKey"]
    logger.info("Reconstituted access key %s for user %s", new_key["AccessKeyId"], rp["username"])
    return {
        "rolled_back": True,
        "rollback_type": "reconstitution",
        "new_access_key_id": new_key["AccessKeyId"],
        "username": rp["username"],
        "note": "Original key is permanently deleted. New key created for same user.",
    }


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    # When called during rollback, parameters contain the prior execution result
    if parameters.get("rollback_type") == "reconstitution" and "rollback_params" in parameters:
        return await _reconstitute(parameters["rollback_params"], connector)

    credential_type = parameters["credential_type"]
    credential_id = parameters.get("credential_id") or parameters.get("access_key_id", "")
    creds = getattr(connector, "credentials", {})

    if not creds:
        logger.info("[mock] Would revoke %s %s", credential_type, credential_id)
        return {"success": True, "mock": True, "rolled_back_available": False}

    if credential_type == "aws_iam_key":
        import boto3
        iam = boto3.client(
            "iam",
            aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
            region_name=creds.get("region", "us-east-1"),
        )
        # Capture state before deletion for reconstitution rollback
        key_info = iam.get_access_key_last_used(AccessKeyId=credential_id)
        username = key_info["UserName"]
        user_info = iam.get_user(UserName=username)
        user_arn = user_info["User"]["Arn"]

        iam.delete_access_key(UserName=username, AccessKeyId=credential_id)
        logger.info("Revoked IAM key %s for user %s", credential_id, username)
        return {
            "success": True,
            "rollback_available": True,
            "rollback_type": "reconstitution",
            "rollback_params": {
                "credential_type": "aws_iam_key",
                "username": username,
                "user_arn": user_arn,
            },
        }

    if credential_type == "vault_token":
        import httpx
        vault_addr = creds.get("vault_addr", "http://localhost:8200")
        vault_token = creds.get("token") or creds.get("vault_token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{vault_addr}/v1/auth/token/revoke",
                headers={"X-Vault-Token": vault_token},
                json={"token": credential_id},
            )
            resp.raise_for_status()
        return {"success": True, "rolled_back_available": False}

    if credential_type == "gcp_service_account_key":
        import json
        import googleapiclient.discovery
        from google.oauth2 import service_account

        sa_info = json.loads(creds["service_account_key_json"])
        gcp_creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        service = googleapiclient.discovery.build("iam", "v1", credentials=gcp_creds)
        # credential_id must be the full resource name:
        # projects/{project}/serviceAccounts/{email}/keys/{key_id}
        service.projects().serviceAccounts().keys().delete(name=credential_id).execute()
        logger.info("Revoked GCP service account key %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    if credential_type == "azure_client_secret":
        import httpx
        parts = credential_id.split("/", 1)
        if len(parts) != 2:
            raise ValueError(
                "azure_client_secret credential_id must be '{app_object_id}/{key_id}'"
            )
        app_id, key_id = parts
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]
            # Retry up to 6× with 5s delay for Azure eventual consistency (400 = not yet propagated)
            import asyncio as _asyncio
            for _attempt in range(6):
                remove_resp = await client.post(
                    f"https://graph.microsoft.com/v1.0/applications/{app_id}/removePassword",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json={"keyId": key_id},
                )
                if remove_resp.status_code == 400:
                    await _asyncio.sleep(5)
                    continue
                remove_resp.raise_for_status()
                break
            else:
                remove_resp.raise_for_status()
        logger.info("Revoked Azure client secret key %s from app %s", key_id, app_id)
        return {"success": True, "rolled_back_available": False}

    if credential_type == "ldap_password":
        import ldap3
        from ldap3 import MODIFY_REPLACE
        server = ldap3.Server(creds["server"])
        with ldap3.Connection(
            server,
            user=creds["bind_dn"],
            password=creds["bind_password"],
            auto_bind=True,
        ) as conn:
            conn.modify(credential_id, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        logger.info("Disabled LDAP account %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    raise ValueError(f"Unsupported credential_type: {credential_type}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    rp = execution_result.get("rollback_params", {})
    if rp.get("credential_type") == "aws_iam_key":
        creds = getattr(connector, "credentials", {})
        if not creds:
            return {"rolled_back": False, "reason": "no credentials available for reconstitution"}
        import boto3
        iam = boto3.client(
            "iam",
            aws_access_key_id=creds.get("access_key_id") or creds.get("aws_access_key_id"),
            aws_secret_access_key=creds.get("secret_access_key") or creds.get("aws_secret_access_key"),
            region_name=creds.get("region", "us-east-1"),
        )
        new_key = iam.create_access_key(UserName=rp["username"])["AccessKey"]
        logger.info("Reconstituted access key %s for user %s", new_key["AccessKeyId"], rp["username"])
        return {
            "rolled_back": True,
            "rollback_type": "reconstitution",
            "new_access_key_id": new_key["AccessKeyId"],
            "username": rp["username"],
            "note": "Original key is permanently deleted. New key created for same user.",
        }
    return {"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}
