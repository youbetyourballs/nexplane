import asyncio
import random
import string
from typing import Any

from app.connectors.catalog_service import get_catalog_service


class ConnectorError(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


async def test_connector(connector_type: str) -> dict:
    await asyncio.sleep(0.1)
    endpoint_map = {
        "aws_mock": "https://mock.aws.nexplane.local",
        "azure_mock": "https://mock.azure.nexplane.local",
        "cloudflare_mock": "https://mock.cloudflare.nexplane.local",
        "okta_mock": "https://mock.okta.nexplane.local",
        "paloalto_mock": "https://mock.paloalto.nexplane.local",
        "ssh_mock": "ssh://mock.runner.nexplane.local",
    }
    return {
        "success": True,
        "latency_ms": random.randint(12, 85),
        "message": f"Mock connector '{connector_type}' is reachable",
        "details": {
            "endpoint": endpoint_map.get(connector_type, "mock://local"),
            "auth_method": "mock_token",
            "permissions_verified": True,
        },
    }


async def execute_action(
    connector_type: str,
    action_id: str,
    parameters: dict,
    asset_ids: list[str],
    connector: Any = None,
) -> dict:
    catalog = get_catalog_service()
    executor = catalog.get_executor(connector_type, action_id)
    return await executor.execute(parameters, asset_ids, connector)


async def execute_rollback(
    change_type: Any,
    rollback_plan: dict,
    execution_result: dict,
) -> dict[str, Any]:
    await asyncio.sleep(0.3)

    strategy = rollback_plan.get("strategy", "manual")

    if strategy == "rollback_unavailable":
        return {
            "rolled_back": False,
            "reason": rollback_plan.get("description", "Rollback not available for this change type"),
            "manual_steps_required": True,
        }

    from datetime import datetime, timezone
    return {
        "rolled_back": True,
        "strategy": strategy,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def run_preflight_checks(preflight_checks: list[dict]) -> dict:
    await asyncio.sleep(0.2)
    results = [{"name": c["name"], "passed": True, "detail": "Check passed"} for c in preflight_checks]
    return {"all_passed": True, "results": results}


async def run_verification_checks(verification_plan: dict, execution_result: dict) -> dict:
    await asyncio.sleep(0.3)
    checks = verification_plan.get("checks", [])
    results = [{"name": c["name"], "passed": True, "detail": f"Mock verification passed: {c['description']}"} for c in checks]
    return {
        "all_passed": True,
        "results": results,
        "success_criteria": verification_plan.get("success_criteria", ""),
    }
