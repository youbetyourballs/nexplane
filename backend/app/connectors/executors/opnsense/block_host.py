from __future__ import annotations
"""Block a specific IP by creating an alias + block rule in OPNsense."""
from datetime import datetime, timezone
from ._client import get_opnsense_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    ip_address = parameters.get("ip_address", "")
    interface = parameters.get("interface", "lan")
    description = parameters.get("description", f"nexplane-block-{ip_address}")
    alias_name = parameters.get("alias_name") or f"nexplane_block_{ip_address.replace('.', '_').replace(':', '_')}"

    if not ip_address:
        raise ValueError("ip_address parameter is required")

    client = get_opnsense_client(connector)
    if not client:
        return {
            "action": "opnsense_block_host",
            "status": "skipped",
            "reason": "no_opnsense_credentials",
        }

    # Step 1: create alias containing the IP
    alias_result = client.add_alias(
        name=alias_name,
        description=description,
        addresses=[ip_address],
    )
    alias_uuid = alias_result.get("uuid")

    # Step 2: create block rule referencing the alias
    rule_result = client.add_filter_rule(
        interface=interface,
        action="block",
        protocol="any",
        source=alias_name,
        destination="any",
        description=description,
    )
    rule_uuid = rule_result.get("uuid")

    return {
        "action": "opnsense_block_host",
        "status": "applied",
        "ip_address": ip_address,
        "alias_name": alias_name,
        "alias_uuid": alias_uuid,
        "rule_uuid": rule_uuid,
        "interface": interface,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    client = get_opnsense_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_opnsense_credentials"}

    errors = []
    rule_uuid = execution_result.get("rule_uuid")
    alias_uuid = execution_result.get("alias_uuid")

    if rule_uuid:
        try:
            client.delete_filter_rule(rule_uuid)
        except Exception as e:
            errors.append(f"rule delete failed: {e}")

    if alias_uuid:
        try:
            client.delete_alias(alias_uuid)
        except Exception as e:
            errors.append(f"alias delete failed: {e}")

    return {
        "rolled_back": len(errors) == 0,
        "deleted_rule_uuid": rule_uuid,
        "deleted_alias_uuid": alias_uuid,
        "errors": errors,
    }
