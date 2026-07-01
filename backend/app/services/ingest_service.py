# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetType, Environment, Criticality
from app.connectors.catalog_service import ActionCatalogService

# Ordered list of metadata keys to try when looking up the external ID.
# First non-null value wins. Covers all connector types.
_METADATA_ID_CANDIDATES = [
    "instance_id", "db_identifier", "bucket_name", "lb_arn",
    "okta_user_id", "object_guid", "device_id", "record_id",
]


def _extract_external_id(payload: dict) -> str | None:
    """Return the stable external ID from the payload, checking top-level 'id'
    then walking known metadata keys."""
    if payload.get("id"):
        return str(payload["id"])
    meta = payload.get("asset_metadata") or {}
    for key in _METADATA_ID_CANDIDATES:
        if meta.get(key):
            return str(meta[key])
    return None


class IngestService:
    def __init__(self, catalog: ActionCatalogService):
        self._catalog = catalog

    async def run(
        self,
        action_id: str,
        connector,
        organization_id: uuid.UUID,
        db: AsyncSession,
    ) -> dict:
        action_def = self._catalog.get_action_def(connector.connector_type, action_id)
        if action_def.get("action_type") != "ingest":
            raise ValueError(f"'{action_id}' is not an ingest action")

        from app.services.connector_service import _attach_credentials
        try:
            await _attach_credentials(connector, db)
        except Exception:
            connector.credentials = {}

        executor = self._catalog.get_executor(connector.connector_type, action_id)
        raw = await executor.execute({}, [], connector)

        if isinstance(raw, dict):
            payloads: list[dict] = raw.get("assets", [])
            auto_asset_payload = raw.get("_auto_asset")
        else:
            payloads = raw
            auto_asset_payload = None

        connector_id = getattr(connector, 'id', None)
        created = 0
        updated = 0
        upserted_assets = []

        if auto_asset_payload:
            from app.services.connector_service import _upsert_auto_asset
            await _upsert_auto_asset(auto_asset_payload, organization_id, db, connector_id=connector_id)

        for payload in payloads:
            name = payload["name"]
            external_id = _extract_external_id(payload)

            base_where = [Asset.organization_id == organization_id]
            if connector_id:
                base_where.append(Asset.connector_id == connector_id)
            else:
                base_where.append(Asset.connector_id.is_(None))

            existing = None

            # Primary dedup: match on any known external ID metadata key
            if external_id:
                for meta_key in _METADATA_ID_CANDIDATES:
                    result = await db.execute(
                        select(Asset).where(
                            *base_where,
                            Asset.asset_metadata[meta_key].as_string() == external_id,
                        )
                    )
                    existing = result.scalars().first()
                    if existing:
                        break

            # Fallback dedup: name match only if the candidate has no external ID yet
            if existing is None:
                result = await db.execute(
                    select(Asset).where(*base_where, Asset.name == name)
                )
                candidate = result.scalars().first()
                if candidate:
                    has_ext_id = any(
                        (candidate.asset_metadata or {}).get(k)
                        for k in _METADATA_ID_CANDIDATES
                    )
                    if not has_ext_id:
                        existing = candidate

            if existing:
                if "asset_metadata" in payload:
                    existing.asset_metadata = {**existing.asset_metadata, **payload["asset_metadata"]}
                if "tags" in payload:
                    existing.tags = list(set(existing.tags or []) | set(payload["tags"]))
                if "criticality" in payload:
                    existing.criticality = Criticality(payload["criticality"])
                if "environment" in payload:
                    existing.environment = Environment(payload["environment"])
                db.add(existing)
                upserted_assets.append(existing)
                updated += 1
            else:
                asset_type_raw = payload.get("asset_type")
                if not asset_type_raw:
                    raise ValueError(f"Payload for asset '{name}' is missing required field 'asset_type'")
                asset = Asset(
                    organization_id=organization_id,
                    connector_id=connector_id,
                    name=name,
                    asset_type=AssetType(asset_type_raw),
                    environment=Environment(payload.get("environment", "prod")),
                    criticality=Criticality(payload.get("criticality", "medium")),
                    asset_metadata=payload.get("asset_metadata", {}),
                    tags=payload.get("tags", []),
                )
                db.add(asset)
                await db.flush()
                upserted_assets.append(asset)
                created += 1

        return {"created": created, "updated": updated, "assets": upserted_assets}
