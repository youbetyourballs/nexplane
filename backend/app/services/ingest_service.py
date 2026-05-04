import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetType, Environment, Criticality
from app.connectors.catalog_service import ActionCatalogService


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

        # Executors may return either a list of asset payloads or a dict with an "assets" key
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
            external_id = payload.get("id") or (payload.get("asset_metadata") or {}).get("instance_id")

            # Dedup by external id (e.g. instance_id) when available, fall back to name.
            # This prevents same-named assets (e.g. two EC2s called "my-new-instance") from colliding.
            base_where = [Asset.organization_id == organization_id]
            if connector_id:
                base_where.append(Asset.connector_id == connector_id)
            else:
                base_where.append(Asset.connector_id.is_(None))

            existing = None
            if external_id:
                result = await db.execute(
                    select(Asset).where(
                        *base_where,
                        Asset.asset_metadata["instance_id"].as_string() == external_id,
                    )
                )
                existing = result.scalar_one_or_none()

            if existing is None:
                result = await db.execute(
                    select(Asset).where(*base_where, Asset.name == name)
                )
                # Only treat a name match as the same asset if it has no external id yet
                candidate = result.scalar_one_or_none()
                if candidate and not (candidate.asset_metadata or {}).get("instance_id"):
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
