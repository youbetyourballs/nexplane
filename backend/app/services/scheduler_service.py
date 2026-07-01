# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")
_db_factory = None


def init_scheduler(db_factory):
    global _db_factory
    _db_factory = db_factory


async def start():
    scheduler.start()
    logger.info("APScheduler started")

    # Register vulnerability background jobs
    scheduler.add_job(
        _run_sla_enforcement,
        trigger="interval",
        minutes=15,
        id="sla_enforcement",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_finding_asset_match,
        trigger="interval",
        minutes=5,
        id="finding_asset_match",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_scanner_poll,
        trigger="interval",
        hours=6,
        id="scanner_poll",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_vuln_sla_escalation,
        trigger="interval",
        minutes=15,
        id="vuln_sla_escalation",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_smoke_reaper,
        trigger="interval",
        minutes=30,
        id="smoke_reaper",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_identity_sync,
        trigger="interval",
        hours=4,
        id="identity_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        _run_kev_refresh,
        trigger="interval",
        hours=24,
        id="cisa_kev_refresh",
        replace_existing=True,
    )

    if _db_factory is None:
        return
    loaded = 0
    try:
        async with _db_factory() as db:
            from app.models.scheduled_ingest import ScheduledIngest
            result = await db.execute(
                select(ScheduledIngest).where(ScheduledIngest.enabled == True)
            )
            schedules = result.scalars().all()
            for s in schedules:
                _register_job(s)
                loaded += 1
    except Exception as e:
        logger.warning(f"Could not load schedules on startup: {e}")
    logger.info(f"Loaded {loaded} scheduled ingest jobs")

    # Load recurring jobs
    loaded_recurring = 0
    try:
        async with _db_factory() as db:
            from app.models.recurring_job import RecurringJob
            from app.services.recurring_job_service import register_job, set_scheduler
            set_scheduler(scheduler)
            result = await db.execute(
                select(RecurringJob).where(RecurringJob.enabled == True)
            )
            jobs = result.scalars().all()
            for job in jobs:
                register_job(job)
                loaded_recurring += 1
    except Exception as e:
        logger.warning(f"Could not load recurring jobs on startup: {e}")
    logger.info(f"Loaded {loaded_recurring} recurring jobs")


def stop():
    if scheduler.running:
        scheduler.shutdown(wait=False)


def _register_job(schedule):
    scheduler.add_job(
        _run_ingest_job,
        trigger="interval",
        hours=schedule.interval_hours,
        id=str(schedule.id),
        args=[str(schedule.id)],
        replace_existing=True,
        next_run_time=schedule.next_run_at or datetime.now(timezone.utc),
    )


def _remove_job(schedule_id: uuid.UUID):
    job_id = str(schedule_id)
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


async def upsert_schedule(schedule):
    _register_job(schedule)


async def remove_schedule(schedule_id: uuid.UUID):
    _remove_job(schedule_id)


async def _run_ingest_job(schedule_id: str):
    if _db_factory is None:
        return
    async with _db_factory() as db:
        from app.models.scheduled_ingest import ScheduledIngest
        from app.models.connector import Connector
        schedule = await db.get(ScheduledIngest, uuid.UUID(schedule_id))
        if not schedule or not schedule.enabled:
            return
        connector = await db.get(Connector, schedule.connector_id)
        if not connector:
            return
        try:
            from app.services.ingest_service import IngestService
            from app.connectors.catalog_service import get_catalog_service
            catalog = get_catalog_service()
            svc = IngestService(catalog)
            await svc.run(
                action_id=schedule.action_id,
                connector=connector,
                organization_id=schedule.organization_id,
                db=db,
            )
            schedule.last_run_status = "success"
            schedule.last_run_error = None
        except Exception as e:
            schedule.last_run_status = "error"
            schedule.last_run_error = str(e)[:500]
            logger.error(f"Scheduled ingest {schedule_id} failed: {e}")
        schedule.last_run_at = datetime.now(timezone.utc)
        schedule.next_run_at = datetime.now(timezone.utc) + timedelta(hours=schedule.interval_hours)
        await db.commit()


async def _run_vuln_sla_escalation():
    from app.jobs.vuln_sla_escalation import run_sla_escalation_for_all_orgs
    await run_sla_escalation_for_all_orgs()


async def _run_sla_enforcement():
    if _db_factory is None:
        return
    async with _db_factory() as db:
        from app.jobs.sla_enforcement import enforce_slas
        await enforce_slas(db)


async def _run_finding_asset_match():
    if _db_factory is None:
        return
    async with _db_factory() as db:
        from app.jobs.finding_asset_match import retry_asset_matching
        await retry_asset_matching(db)


async def _run_scanner_poll():
    if _db_factory is None:
        return
    from app.models.organization import Organization
    from sqlalchemy import select
    async with _db_factory() as db:
        from app.jobs.scanner_poll import poll_crowdstrike
        result = await db.execute(select(Organization.id))
        org_ids = [row[0] for row in result]
    for org_id in org_ids:
        async with _db_factory() as db:
            await poll_crowdstrike(db, org_id)


async def _run_identity_sync():
    if _db_factory is None:
        return
    async with _db_factory() as db:
        try:
            from app.services.identity_sync_service import sync_all
            stats = await sync_all(db)
            await db.commit()
            logger.info("Identity sync completed: %s", stats)
        except Exception as exc:
            logger.error("Identity sync failed: %s", exc)


async def _run_kev_refresh():
    from app.services.vuln_poc_service import refresh_kev_cache
    try:
        await refresh_kev_cache()
        # After refreshing KEV, bulk-enrich all orgs' open findings
        if _db_factory:
            await _run_bulk_kev_enrich()
    except Exception as exc:
        logger.warning("CISA KEV refresh job failed: %s", exc)


async def _run_bulk_kev_enrich():
    """After a KEV cache refresh, cross-reference all open CVE findings across all orgs."""
    from app.services.vuln_poc_service import check_cisa_kev, apply_poc_result
    from app.models.vulnerability import VulnerabilityFinding
    try:
        async with _db_factory() as db:
            result = await db.execute(
                select(VulnerabilityFinding).where(
                    VulnerabilityFinding.status.in_(["open", "actionable"]),
                    VulnerabilityFinding.cve_id.isnot(None),
                    VulnerabilityFinding.poc_source.is_(None),
                )
            )
            findings = result.scalars().all()
            updated = sum(
                1 for f in findings
                if check_cisa_kev(f.cve_id) and (apply_poc_result(f, "exploited", "cisa_kev", f.cve_id) or True)
            )
            if updated:
                await db.commit()
            logger.info("KEV bulk enrich: %d/%d findings updated", updated, len(findings))
    except Exception as exc:
        logger.warning("KEV bulk enrich job failed: %s", exc)


async def _run_smoke_reaper():
    if _db_factory is None:
        return
    from app.services.smoke_reaper import reap_smoke_zombies
    await reap_smoke_zombies(_db_factory)


async def dispatch_due_reboots():
    """Dispatch scheduled_reboot CRs whose reboot_at time has passed."""
    if _db_factory is None:
        return
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus

    async with _db_factory() as db:
        result = await db.execute(
            select(ChangeRequest).where(
                ChangeRequest.change_type == ChangeType.scheduled_reboot,
                ChangeRequest.status == ChangeRequestStatus.approved,
            )
        )
        crs = result.scalars().all()

    now = datetime.now(timezone.utc)
    for cr in crs:
        reboot_at_str = (cr.metadata or {}).get("reboot_at") if hasattr(cr, "metadata") else None
        if not reboot_at_str:
            continue
        try:
            reboot_at = datetime.fromisoformat(reboot_at_str)
            if reboot_at.tzinfo is None:
                reboot_at = reboot_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        if reboot_at <= now:
            target = getattr(cr, "target_asset_id", None)
            if target:
                await _dispatch_agent_reboot_job(target, cr)


async def _dispatch_agent_reboot_job(asset_id: str, cr) -> None:
    """Dispatch a reboot job via agent for a given asset."""
    pass  # Implemented when agent reboot executor is wired


async def check_access_review_schedules():
    """Create AccessReview records for overdue review schedules."""
    if _db_factory is None:
        return
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import text
    from app.models.access_review import AccessReview

    async with _db_factory() as db:
        result = await db.execute(text("SELECT * FROM access_review_schedules WHERE enabled = true") if True else None)
        schedules = result.scalars().all()

        now = datetime.now(timezone.utc)
        for sched in schedules:
            if not getattr(sched, "enabled", True):
                continue
            freq_days = getattr(sched, "frequency_days", None)
            last_at = getattr(sched, "last_review_created_at", None)
            if freq_days is None:
                continue
            if last_at and (now - last_at) < timedelta(days=freq_days):
                continue
            review = AccessReview(
                id=uuid.uuid4(),
                created_by=getattr(sched, "created_by", uuid.uuid4()),
                title=f"Scheduled Review {now.date().isoformat()}",
                scope={},
                snapshot={},
                decisions={},
            )
            db.add(review)
        await db.commit()


async def run_weekly_compliance_scans():
    """Dispatch CIS audit agent jobs to all managed Linux server assets."""
    if _db_factory is None:
        return
    from sqlalchemy import select
    from app.models.asset import Asset, AssetType

    async with _db_factory() as db:
        result = await db.execute(
            select(Asset).where(Asset.asset_type == AssetType.server)
        )
        assets = result.scalars().all()

    for asset in assets:
        await _dispatch_cis_audit_job(asset.id)


async def _dispatch_cis_audit_job(asset_id) -> None:
    """Dispatch a CIS audit job for a given asset."""
    pass  # Implemented when compliance scheduler is wired


async def promote_queued_changes(db) -> None:
    """Promote queued_for_maintenance CRs when their maintenance window opens."""
    from sqlalchemy import select, text
    from app.models.change_request import ChangeRequest, ChangeRequestStatus

    result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.status == ChangeRequestStatus.queued_for_maintenance
        )
    )
    crs = result.scalars().all()
    # For each CR, check if a maintenance window is open; if so, promote to approved
    for cr in crs:
        pass  # Window check and promotion logic wired when maintenance window service is active
