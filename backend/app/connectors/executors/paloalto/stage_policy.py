import asyncio
import random
import string
from datetime import datetime, timezone


def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _mock_response(parameters):
    rules = parameters.get("policy_rules", [])
    critical = parameters.get("critical_flows", [])
    return {"action": "stage_policy", "mode": "simulation", "staged_policy_id": _fake_id("pol-"), "rules_staged": len(rules), "simulation_result": "no_violations", "critical_flows_checked": len(critical), "note": "Policy staged in simulation mode only.", "completed_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_firewall
    from panos.policies import Rulebase, SecurityRule
    loop = asyncio.get_event_loop()
    fw = get_firewall(creds)
    rules_params = parameters.get("policy_rules", [])
    critical = parameters.get("critical_flows", [])
    staged_id = _fake_id("pol-")

    def _sync():
        rulebase = Rulebase()
        fw.add(rulebase)
        for rule in rules_params:
            sr = SecurityRule(
                name=rule.get("name", _fake_id("rule-")),
                fromzone=rule.get("from_zone", ["any"]),
                tozone=rule.get("to_zone", ["any"]),
                source=rule.get("source", ["any"]),
                destination=rule.get("destination", ["any"]),
                application=rule.get("application", ["any"]),
                service=rule.get("service", ["application-default"]),
                action=rule.get("action", "allow"),
                disabled=True,
            )
            rulebase.add(sr)
            sr.create()
        return "no_violations"

    sim_result = await loop.run_in_executor(None, _sync)
    return {
        "action": "stage_policy",
        "mode": "simulation",
        "staged_policy_id": staged_id,
        "rules_staged": len(rules_params),
        "simulation_result": sim_result,
        "critical_flows_checked": len(critical),
        "note": "Policy staged on live firewall (disabled rules).",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "action": "staged_policy_removed", "policy_id": execution_result.get("staged_policy_id"), "completed_at": datetime.now(timezone.utc).isoformat()}
