from __future__ import annotations
"""Create or update an OPNsense firewall filter rule; rollback deletes it."""
from datetime import datetime, timezone
from ._client import get_opnsense_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    interface = parameters.get("interface", "lan")
    action = parameters.get("action", "pass")
    protocol = parameters.get("protocol", "any")
    source = parameters.get("source", "any")
    destination = parameters.get("destination", "any")
    destination_port = parameters.get("destination_port", "any")
    description = parameters.get("description", "nexplane-managed rule")

    client = get_opnsense_client(connector)
    if not client:
        return {
            "action": "opnsense_update_rule",
            "status": "skipped",
            "reason": "no_opnsense_credentials",
        }

    result = client.add_filter_rule(
        interface=interface,
        action=action,
        protocol=protocol,
        source=source,
        destination=destination,
        description=description,
        destination_port=destination_port,
    )
    rule_uuid = result.get("uuid") or (result.get("result", {}).get("uuid") if isinstance(result.get("result"), dict) else None)

    return {
        "action": "opnsense_update_rule",
        "status": "applied",
        "rule_uuid": rule_uuid,
        "interface": interface,
        "action_type": action,
        "source": source,
        "destination": destination,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    rule_uuid = execution_result.get("rule_uuid")
    if not rule_uuid:
        return {"rolled_back": False, "reason": "no rule_uuid in execution_result"}

    client = get_opnsense_client(connector)
    if not client:
        return {"rolled_back": False, "reason": "no_opnsense_credentials"}

    client.delete_filter_rule(rule_uuid)
    return {"rolled_back": True, "deleted_rule_uuid": rule_uuid}
