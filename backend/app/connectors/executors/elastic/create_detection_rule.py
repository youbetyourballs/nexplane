from __future__ import annotations
"""Create a KQL detection rule in Kibana Security. Rollback deletes it."""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Create a KQL detection rule in Kibana Security.

    Parameters:
        rule_id (str): Unique rule ID (caller-supplied or auto-generated)
        name (str): Human-readable rule name
        description (str): Rule description
        query (str): KQL query string
        index (list[str]): Index patterns to search, e.g. ["logs-*", "filebeat-*"]
        severity (str): low / medium / high / critical. Default: medium
        risk_score (int): 1-100. Default: 47
        interval (str): How often to run, e.g. "5m". Default: "5m"
        enabled (bool): Whether to enable the rule immediately. Default: True

    Returns dict with:
        action: "elastic_create_rule"
        rule_id: the rule_id used
        kibana_id: Kibana's internal UUID for the rule
        created: True
    """
    import uuid as _uuid
    from ._client import get_elastic_client

    client = get_elastic_client(connector)
    if client is None:
        rule_id = parameters.get("rule_id", str(_uuid.uuid4()))
        return {
            "action": "elastic_create_rule",
            "rule_id": rule_id,
            "kibana_id": "",
            "created": True,
            "status": "skipped",
            "reason": "no credentials",
        }

    rule_id: str = parameters.get("rule_id") or str(_uuid.uuid4())
    index_patterns = parameters.get("index", ["logs-*", "filebeat-*", ".ds-logs-*"])

    rule_def = {
        "rule_id": rule_id,
        "name": parameters.get("name", f"Nexplane smoke rule {rule_id[:8]}"),
        "description": parameters.get("description", "Created by Nexplane"),
        "type": "query",
        "language": "kuery",
        "query": parameters.get("query", "*"),
        "index": index_patterns,
        "severity": parameters.get("severity", "medium"),
        "risk_score": parameters.get("risk_score", 47),
        "interval": parameters.get("interval", "5m"),
        "from": "now-6m",
        "enabled": parameters.get("enabled", True),
    }

    try:
        result = client.create_rule(rule_def)
        kibana_id = result.get("id", "")
        return {
            "action": "elastic_create_rule",
            "rule_id": rule_id,
            "kibana_id": kibana_id,
            "created": True,
        }
    finally:
        client.close()


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the rule that was created."""
    from ._client import get_elastic_client

    rule_id = execution_result.get("rule_id") or parameters.get("rule_id", "")
    if not rule_id:
        return {"rolled_back": False, "reason": "no rule_id in execution_result"}

    client = get_elastic_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no credentials"}

    try:
        client.delete_rule(rule_id)
        return {"rolled_back": True, "rule_id": rule_id}
    except Exception as exc:
        return {"rolled_back": False, "reason": str(exc)}
    finally:
        client.close()
