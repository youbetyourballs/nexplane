# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
from typing import Any

from app.connectors.catalog_service import get_catalog_service


class ConnectorError(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


async def test_connector(connector_type: str) -> dict:
    await asyncio.sleep(0.1)
    endpoint_map = {
        "aws": "https://ec2.amazonaws.com",
        "azure": "https://management.azure.com",
        "cloudflare": "https://api.cloudflare.com",
        "okta": "https://api.okta.com",
        "paloalto": "https://firewall.local",
        "ssh": "ssh://runner.local",
        "active_directory": "ldap://ad.local",
        "crowdstrike": "https://api.crowdstrike.com",
        "tenable": "https://cloud.tenable.com",
        "nexplane_agent": "agent://local",
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


async def _attach_credentials(connector, db) -> None:
    """Decrypt and attach credentials dict to connector object."""
    from sqlalchemy import select
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend

    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        backend = get_secret_backend()
        connector.credentials = backend.decrypt_json(cred_row.credentials_encrypted)
    else:
        connector.credentials = {}


async def execute_action(
    connector_type: str,
    action_id: str,
    parameters: dict,
    asset_ids: list[str],
    connector: Any = None,
    db=None,
) -> dict:
    if connector is not None and db is not None:
        await _attach_credentials(connector, db)
    catalog = get_catalog_service()
    try:
        executor = catalog.get_executor(connector_type, action_id)
    except (KeyError, ImportError, ValueError) as exc:
        raise ConnectorError(str(exc), {"connector_type": connector_type, "action_id": action_id}) from exc
    result = await executor.execute(parameters, asset_ids, connector)
    if "_auto_asset" in result and connector is not None and db is not None:
        connector_id = getattr(connector, 'id', None)
        await _upsert_auto_asset(result.pop("_auto_asset"), connector.organization_id, db, connector_id=connector_id)
    return result


async def _upsert_auto_asset(payload: dict, organization_id, db, connector_id=None) -> None:
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType, Environment, Criticality
    name = payload.get("name", "unnamed")

    if connector_id:
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.connector_id == connector_id,
                Asset.name == name,
            )
        )
    else:
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.connector_id.is_(None),
                Asset.name == name,
            )
        )
    existing = result.scalars().first()
    if existing:
        existing.asset_metadata = {**existing.asset_metadata, **payload.get("asset_metadata", {})}
        existing.tags = list(set(existing.tags or []) | set(payload.get("tags", [])))
        db.add(existing)
    else:
        asset = Asset(
            organization_id=organization_id,
            connector_id=connector_id,
            name=name,
            asset_type=AssetType(payload.get("asset_type", "server")),
            environment=Environment(payload.get("environment", "prod")),
            criticality=Criticality(payload.get("criticality", "medium")),
            asset_metadata=payload.get("asset_metadata", {}),
            tags=payload.get("tags", []),
        )
        db.add(asset)
        await db.flush()


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
