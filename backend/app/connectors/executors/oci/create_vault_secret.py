import asyncio
import base64
from datetime import datetime, timezone
from ._client import get_vault_client, _make_config


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = (
        parameters.get("compartment_id")
        or creds.get("compartment_id")
        or creds.get("tenancy_id")
        or creds.get("tenancy", "")
    )
    secret_name = parameters.get("secret_name", "nexplane-secret")
    secret_content = parameters.get("secret_content", "changeme")
    description = parameters.get("description", "Created by Nexplane")

    auto_asset = {
        "name": secret_name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "critical",
        "asset_metadata": {
            "secret_name": secret_name,
            "compartment_id": compartment_id,
            "provider": "oci",
        },
        "tags": ["oci", "oci-vault-secret", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_vault_secret",
            "secret_name": secret_name,
            "secret_id": "ocid1.vaultsecret.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    vault_client = get_vault_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        import oci.pagination
        # Preflight: find active vault
        vaults = oci.pagination.list_call_get_all_results(
            vault_client.list_vaults, compartment_id=compartment_id
        ).data
        active_vaults = [v for v in vaults if v.lifecycle_state == "ACTIVE"]
        if not active_vaults:
            raise ValueError(
                "No ACTIVE Vault in this compartment. Create a Vault in the OCI Console first."
            )
        vault = active_vaults[0]
        vault_id = parameters.get("vault_id") or vault.id

        # Resolve master encryption key
        from oci.key_management import KmsManagementClient
        kms_client = KmsManagementClient(
            _make_config(creds), service_endpoint=vault.management_endpoint
        )
        keys = oci.pagination.list_call_get_all_results(
            kms_client.list_keys, compartment_id=compartment_id
        ).data
        active_keys = [k for k in keys if k.lifecycle_state == "ENABLED"]
        if not active_keys:
            raise ValueError("No ENABLED master encryption key found in vault.")
        key_id = parameters.get("key_id") or active_keys[0].id

        encoded = base64.b64encode(secret_content.encode()).decode()
        content_details = oci.vault.models.Base64SecretContentDetails(
            content_type=oci.vault.models.SecretContentDetails.CONTENT_TYPE_BASE64,
            name="v1",
            stage="CURRENT",
            content=encoded,
        )
        secret_details = oci.vault.models.CreateSecretDetails(
            compartment_id=compartment_id,
            vault_id=vault_id,
            key_id=key_id,
            secret_name=secret_name,
            description=description,
            secret_content=content_details,
        )
        return vault_client.create_secret(secret_details).data

    secret = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["secret_id"] = secret.id
    auto_asset["asset_metadata"]["vault_id"] = secret.vault_id
    return {
        "action": "create_vault_secret",
        "secret_name": secret_name,
        "secret_id": secret.id,
        "vault_id": secret.vault_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_vault_secret import execute as delete
    return await delete(
        {"secret_id": execution_result.get("secret_id"), "deletion_time_days": 1},
        [],
        connector,
    )
