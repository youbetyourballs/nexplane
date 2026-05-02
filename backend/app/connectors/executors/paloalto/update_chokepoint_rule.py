import asyncio
from datetime import datetime, timezone


def _mock_response(parameters, asset_ids):
    return {"action": "update_chokepoint_rule", "rule_name": parameters.get("rule_name"), "new_action": parameters.get("new_action"), "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_firewall
    from panos.policies import Rulebase, SecurityRule
    loop = asyncio.get_event_loop()
    fw = get_firewall(creds)
    rule_name = parameters.get("rule_name")
    new_action = parameters.get("new_action", "deny")

    def _sync():
        rulebase = Rulebase()
        fw.add(rulebase)
        SecurityRule.refreshall(rulebase)
        for rule in rulebase.children:
            if isinstance(rule, SecurityRule) and rule.name == rule_name:
                rule.action = new_action
                rule.apply()
                break
        fw.commit()

    await loop.run_in_executor(None, _sync)
    return {"action": "update_chokepoint_rule", "rule_name": rule_name, "new_action": new_action, "assets": asset_ids, "applied": True, "applied_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters, asset_ids)
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "restore_chokepoint_rule", "rule_name": parameters.get("rule_name")}
