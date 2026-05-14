from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.vulnerability import VulnerabilityFinding
from app.models.change_request import ChangeRequest


async def close_linked_findings(db: AsyncSession, cr_id: str) -> int:
    """Mark findings linked to this CR as remediated. Returns count closed."""
    cr_uuid = uuid.UUID(cr_id)
    cr = await db.get(ChangeRequest, cr_uuid)
    if not cr or not cr.finding_ids:
        return 0
    count = 0
    for fid in cr.finding_ids:
        try:
            result = await db.execute(
                select(VulnerabilityFinding).where(VulnerabilityFinding.id == uuid.UUID(fid))
            )
            finding = result.scalar_one_or_none()
            if finding and getattr(finding, "status", "open") == "open":
                finding.status = "remediated"
                finding.remediated_at = datetime.now(timezone.utc)
                finding.remediated_by_cr_id = cr_uuid
                count += 1
        except Exception:
            pass
    if count > 0:
        await db.commit()
    return count
