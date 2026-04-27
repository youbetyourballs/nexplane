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

        executor = self._catalog.get_executor(connector.connector_type, action_id)
        payloads: list[dict] = await executor.execute({}, [], connector)

        created = 0
        updated = 0
        upserted_assets = []

        for payload in payloads:
            name = payload["name"]
            result = await db.execute(
                select(Asset).where(
                    Asset.organization_id == organization_id,
                    Asset.name == name,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                if "asset_metadata" in payload:
                    existing.asset_metadata = {**existing.asset_metadata, **payload["asset_metadata"]}
                if "tags" in payload:
                    merged = list(set(existing.tags or []) | set(payload["tags"]))
                    existing.tags = merged
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
