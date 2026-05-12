"""Patch campaign execution service."""
from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _build_batches(asset_ids: list[str], batch_size: int, strategy: str) -> list[list[str]]:
    if strategy == "canary" and len(asset_ids) > 1:
        return [[asset_ids[0]], asset_ids[1:]]
    if strategy == "all_at_once":
        return [asset_ids]
    return [asset_ids[i:i + batch_size] for i in range(0, len(asset_ids), batch_size)]


def _should_abort(batch_result: dict, threshold: float) -> bool:
    asset_ids = batch_result.get("asset_ids", [])
    failed_ids = batch_result.get("failed_ids", [])
    if not asset_ids:
        return False
    return (len(failed_ids) / len(asset_ids)) > threshold


async def execute_campaign(campaign_id: str) -> None:
    from app.database import AsyncSessionLocal
    from app.models.patch_campaign import PatchCampaign, CampaignStatus
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus, RiskLevel
    from app.models.vulnerability import VulnerabilityFinding
    from app.models.user import User
    from app.workflows.execute_change_workflow import execute_change_workflow
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        campaign = await db.get(PatchCampaign, uuid.UUID(campaign_id))
        if not campaign:
            logger.error(f"Campaign {campaign_id} not found")
            return
        campaign.status = CampaignStatus.running
        campaign.started_at = datetime.now(timezone.utc)
        await db.commit()

    batches = _build_batches(
        [str(a) for a in campaign.target_asset_ids],
        campaign.batch_size,
        campaign.rollout_strategy,
    )
    batch_results = []

    for i, batch_asset_ids in enumerate(batches):
        async with AsyncSessionLocal() as db:
            camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
            if camp and camp.status == CampaignStatus.aborted:
                return
            if camp and camp.status == CampaignStatus.paused:
                for _ in range(360):
                    await asyncio.sleep(10)
                    await db.refresh(camp)
                    if camp.status != CampaignStatus.paused:
                        break

        cr_ids, failed_ids = [], []
        for asset_id in batch_asset_ids:
            try:
                async with AsyncSessionLocal() as db:
                    # Get package info from finding if available
                    finding_r = await db.execute(
                        select(VulnerabilityFinding).where(VulnerabilityFinding.asset_id == uuid.UUID(asset_id)).limit(1)
                    )
                    finding = finding_r.scalars().first()
                    pkg = (finding.affected_package or "") if finding else ""
                    target_ver = (finding.fixed_version or "") if finding else ""

                    # Get a real user for requester_id (E4 fix)
                    user_r = await db.execute(
                        select(User).where(User.organization_id == campaign.organization_id).limit(1)
                    )
                    system_user = user_r.scalars().first()
                    if not system_user:
                        logger.warning(f"No user found for org {campaign.organization_id}, skipping asset {asset_id}")
                        failed_ids.append(asset_id)
                        continue

                    cr = ChangeRequest(
                        organization_id=campaign.organization_id,
                        requester_id=system_user.id,
                        change_type=ChangeType.patch_packages,
                        title=f"[Campaign] Patch {pkg or 'packages'} on {asset_id[:8]}",
                        description=f"Part of campaign {campaign_id}",
                        risk_level=RiskLevel.medium,
                        target_asset_ids=[asset_id],
                        desired_outcome={"packages": [{"name": pkg, "target_version": target_ver}]} if pkg else {},
                        status=ChangeRequestStatus.pending,
                    )
                    db.add(cr)
                    await db.commit()
                    await db.refresh(cr)
                    cr_ids.append(str(cr.id))

                asyncio.create_task(execute_change_workflow(str(cr.id)))
            except Exception as e:
                logger.warning(f"Campaign {campaign_id} asset {asset_id} CR failed: {e}")
                failed_ids.append(asset_id)

        # Wait for CRs to complete (up to 10 min)
        for _ in range(120):
            await asyncio.sleep(5)
            all_done = True
            async with AsyncSessionLocal() as db:
                for cr_id in cr_ids:
                    cr = await db.get(ChangeRequest, uuid.UUID(cr_id))
                    if cr and cr.status not in ("completed", "failed", "rolled_back", "completed_with_errors"):
                        all_done = False
                        break
                    if cr and cr.status in ("failed", "rolled_back"):
                        for aid in (cr.target_asset_ids or []):
                            if aid not in failed_ids:
                                failed_ids.append(aid)
            if all_done:
                break

        batch_result = {"batch_index": i, "asset_ids": batch_asset_ids, "cr_ids": cr_ids, "failed_ids": failed_ids, "status": "failed" if failed_ids else "passed"}
        batch_results.append(batch_result)

        await asyncio.sleep(min(campaign.health_gate_seconds, 10))  # cap for tests

        if _should_abort(batch_result, campaign.abort_threshold) and i < len(batches) - 1:
            async with AsyncSessionLocal() as db:
                camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
                if camp:
                    camp.status = CampaignStatus.failed
                    camp.batches = batch_results
                    camp.completed_at = datetime.now(timezone.utc)
                    await db.commit()
            return

        async with AsyncSessionLocal() as db:
            camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
            if camp:
                camp.batches = batch_results
                await db.commit()

    any_failed = any(b["failed_ids"] for b in batch_results)
    async with AsyncSessionLocal() as db:
        camp = await db.get(PatchCampaign, uuid.UUID(campaign_id))
        if camp:
            camp.status = CampaignStatus.failed if any_failed else CampaignStatus.complete
            camp.batches = batch_results
            camp.completed_at = datetime.now(timezone.utc)
            await db.commit()
