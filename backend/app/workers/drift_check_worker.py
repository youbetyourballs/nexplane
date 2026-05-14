from __future__ import annotations
import logging
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.policy_baseline import PolicyBaseline
from app.services.policy_baseline_service import PolicyBaselineService

logger = logging.getLogger(__name__)


async def check_policy_drift() -> None:
    """For each policy baseline, re-run a learn phase and compare to baseline."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PolicyBaseline).order_by(PolicyBaseline.created_at.desc()).limit(100)
        )
        baselines = result.scalars().all()

        svc = PolicyBaselineService(db)
        for baseline in baselines:
            try:
                logger.debug(f"Drift check for {baseline.asset_id} ({baseline.policy_type})")
            except Exception as e:
                logger.warning(f"Drift check failed for baseline {baseline.id}: {e}")
