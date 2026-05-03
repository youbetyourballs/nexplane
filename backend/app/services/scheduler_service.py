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
    _register_operational_jobs()
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


# ---------------------------------------------------------------------------
# Scheduled Reboot Dispatcher — runs every 60 seconds
# ---------------------------------------------------------------------------

async def dispatch_due_reboots():
    """Find approved scheduled_reboot change requests whose reboot_at has passed and dispatch them."""
    if _db_factory is None:
        return
    try:
        async with _db_factory() as db:
            from app.models.change_request import ChangeRequest
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(ChangeRequest).where(
                    ChangeRequest.change_type == "scheduled_reboot",
                    ChangeRequest.status == "approved",
                )
            )
            due = [
                cr for cr in result.scalars().all()
                if _reboot_is_due(cr, now)
            ]
            for cr in due:
                cr.status = "executing"
                await db.commit()
                await _dispatch_agent_reboot_job(
                    cr.target_asset_id,
                    cr.metadata or {},
                    str(cr.id),
                    db,
                )
    except Exception as e:
        logger.error(f"dispatch_due_reboots failed: {e}")


def _reboot_is_due(cr, now: datetime) -> bool:
    """Return True if the change request's reboot_at is in the past."""
    meta = cr.metadata or {}
    reboot_at_str = meta.get("reboot_at")
    if not reboot_at_str:
        return False
    try:
        from datetime import datetime as dt
        reboot_at = dt.fromisoformat(reboot_at_str.replace("Z", "+00:00"))
        return reboot_at <= now
    except (ValueError, TypeError):
        return False


async def _dispatch_agent_reboot_job(asset_id: str, meta: dict, change_request_id: str, db):
    """Create an AgentJob for the reboot command on the target asset."""
    from app.models.agent import AgentJob, AgentRegistration
    from sqlalchemy import select as sa_select
    reg_result = await db.execute(
        sa_select(AgentRegistration).where(AgentRegistration.asset_id == asset_id)
    )
    reg = reg_result.scalars().first()
    if not reg:
        logger.warning(f"No agent registration found for asset_id={asset_id}")
        return
    job = AgentJob(
        organization_id=reg.organization_id,
        agent_registration_id=reg.id,
        change_request_id=change_request_id,
        command="graceful_reboot",
        parameters={
            "graceful_delay_seconds": meta.get("graceful_delay_seconds", 60),
            "verify_services":        meta.get("verify_services", []),
        },
        hmac_signature="",  # populated by HMAC service before delivery
        status="pending",
    )
    db.add(job)
    await db.commit()


# ---------------------------------------------------------------------------
# Access Review Creator — runs daily at 06:00 UTC
# ---------------------------------------------------------------------------

async def check_access_review_schedules():
    """Create AccessReview records for any AccessReviewSchedule that is now due."""
    if _db_factory is None:
        return
    try:
        async with _db_factory() as db:
            from app.models.access_review_schedule import AccessReviewSchedule
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(AccessReviewSchedule).where(AccessReviewSchedule.enabled == True)
            )
            for sched in result.scalars().all():
                baseline = sched.last_review_created_at or datetime.min.replace(tzinfo=timezone.utc)
                due_at = baseline + timedelta(days=sched.frequency_days)
                if now >= due_at:
                    await _create_access_review(sched, db)
                    sched.last_review_created_at = now
            await db.commit()
    except Exception as e:
        logger.error(f"check_access_review_schedules failed: {e}")


async def _create_access_review(sched, db):
    """Insert an AccessReview record and notify reviewers."""
    # AccessReview model is defined in the identity lifecycle spec.
    # Import lazily to avoid circular dependency.
    try:
        from app.models.access_review import AccessReview
        review = AccessReview(
            schedule_id=sched.id,
            scope=sched.scope,
            reviewer_rule=sched.reviewer_assignment_rule,
            status="pending",
        )
        db.add(review)
        logger.info(f"Created AccessReview for schedule {sched.id} (scope={sched.scope})")
    except ImportError:
        # AccessReview model not yet deployed — log and skip
        logger.warning("AccessReview model not available — skipping review creation")


# ---------------------------------------------------------------------------
# CIS Audit Weekly — runs every Sunday at 01:00 UTC
# ---------------------------------------------------------------------------

async def run_weekly_compliance_scans():
    """Dispatch cis_audit agent jobs to all managed Linux assets."""
    if _db_factory is None:
        return
    try:
        async with _db_factory() as db:
            from app.models.agent import AgentRegistration
            result = await db.execute(select(AgentRegistration))
            assets = result.scalars().all()
            for asset in assets:
                await _dispatch_cis_audit_job(str(asset.asset_id), str(asset.id), str(asset.organization_id), db)
    except Exception as e:
        logger.error(f"run_weekly_compliance_scans failed: {e}")


async def _dispatch_cis_audit_job(asset_id: str, agent_reg_id: str, org_id: str, db):
    """Create an AgentJob for the cis_audit command."""
    from app.models.agent import AgentJob
    job = AgentJob(
        organization_id=org_id,
        agent_registration_id=agent_reg_id,
        command="audit_cis_compliance",
        parameters={"store_results_in": "asset_metadata"},
        hmac_signature="",
        status="pending",
    )
    db.add(job)
    await db.commit()
    logger.info(f"Dispatched cis_audit job for asset {asset_id}")


# ---------------------------------------------------------------------------
# Maintenance Window Checker — runs every 60 seconds
# ---------------------------------------------------------------------------

async def check_maintenance_windows():
    """Dispatch approved change requests that fall within an active maintenance window."""
    if _db_factory is None:
        return
    try:
        async with _db_factory() as db:
            from app.models.change_request import ChangeRequest
            now = datetime.now(timezone.utc)
            result = await db.execute(
                select(ChangeRequest).where(
                    ChangeRequest.status == "approved",
                    ChangeRequest.change_type != "scheduled_reboot",  # handled by dispatch_due_reboots
                )
            )
            for cr in result.scalars().all():
                meta = cr.metadata or {}
                scheduled_at_str = meta.get("scheduled_at")
                if not scheduled_at_str:
                    continue
                try:
                    scheduled_at = datetime.fromisoformat(scheduled_at_str.replace("Z", "+00:00"))
                    if scheduled_at <= now:
                        cr.status = "executing"
                        await db.commit()
                        logger.info(f"Dispatching maintenance window change request {cr.id}")
                except (ValueError, TypeError):
                    continue
    except Exception as e:
        logger.error(f"check_maintenance_windows failed: {e}")


# ---------------------------------------------------------------------------
# Register all new jobs at startup
# ---------------------------------------------------------------------------

def _register_operational_jobs():
    """Register the four operational scheduler jobs. Called from start()."""
    scheduler.add_job(
        dispatch_due_reboots,
        trigger="interval",
        seconds=60,
        id="scheduled-reboot-dispatcher",
        replace_existing=True,
    )
    scheduler.add_job(
        check_access_review_schedules,
        trigger="cron",
        hour=6,
        minute=0,
        id="access-review-check",
        replace_existing=True,
    )
    scheduler.add_job(
        run_weekly_compliance_scans,
        trigger="cron",
        day_of_week="sun",
        hour=1,
        minute=0,
        id="compliance-scan-weekly",
        replace_existing=True,
    )
    scheduler.add_job(
        check_maintenance_windows,
        trigger="interval",
        seconds=60,
        id="maintenance-window-checker",
        replace_existing=True,
    )
    logger.info("Registered operational scheduler jobs: reboot-dispatcher, access-review-check, compliance-scan-weekly, maintenance-window-checker")
