"""
Seed asset relationships from asset_metadata["depends_on"] lists.
Idempotent: skips an org if it already has any relationships.
"""
from __future__ import annotations
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.asset_relationship import AssetRelationship

DEMO_ORG_IDS = [
    uuid.UUID("00000000-0000-0000-0000-000000000001"),
    uuid.UUID("00000000-0000-0000-0000-000000000002"),
    uuid.UUID("00000000-0000-0000-0000-000000000003"),
    uuid.UUID("00000000-0000-0000-0000-000000000004"),
]


async def seed_asset_graph(db: AsyncSession) -> None:
    for org_id in DEMO_ORG_IDS:
        existing = await db.execute(
            select(AssetRelationship)
            .where(AssetRelationship.organization_id == org_id)
            .limit(1)
        )
        if existing.scalar_one_or_none():
            print(f"Asset graph seed: org {org_id} already seeded, skipping.")
            continue

        assets_result = await db.execute(
            select(Asset).where(Asset.organization_id == org_id)
        )
        assets = assets_result.scalars().all()
        name_to_id: dict[str, uuid.UUID] = {a.name.lower(): a.id for a in assets}

        created = 0
        for asset in assets:
            depends_on_list = (asset.asset_metadata or {}).get("depends_on", [])
            if not isinstance(depends_on_list, list):
                continue
            for dep_name in depends_on_list:
                if not isinstance(dep_name, str) or not dep_name.strip():
                    continue
                target_id = name_to_id.get(dep_name.strip().lower())
                if target_id is None or target_id == asset.id:
                    continue
                rel = AssetRelationship(
                    organization_id=org_id,
                    source_asset_id=asset.id,
                    target_asset_id=target_id,
                    relationship_type="depends_on",
                    rel_metadata={},
                    created_by=None,
                )
                db.add(rel)
                created += 1

        await db.flush()
        print(f"Asset graph seed: org {org_id} — created {created} relationships.")

    await db.commit()
    print("Asset graph seed complete.")
