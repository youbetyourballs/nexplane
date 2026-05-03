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

    # Register fleet maintenance window promotion job (every 60 seconds)
    scheduler.add_job(
        promote_queued_changes,
        trigger="interval",
        seconds=60,
        id="promote_queued_changes",
        replace_existing=True,
    )


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


async def promote_queued_changes(db=None):
    """
    Run every 60 seconds. Find change_requests in 'queued_for_maintenance' status
    and re-promote them to 'approved' if a maintenance window is currently open for
    their target assets, then trigger execution.
    """
    if db is None:
        if _db_factory is None:
            return
        async with _db_factory() as session:
            await _do_promote(session)
        return
    await _do_promote(db)


async def _do_promote(db):
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from app.models.maintenance_window import MaintenanceWindow
    from app.models.asset import Asset
    from app.routers.maintenance_windows import is_window_open_for_org

    now = datetime.now(timezone.utc)

    windows_result = await db.execute(
        select(MaintenanceWindow).where(MaintenanceWindow.enabled == True)
    )
    windows = windows_result.scalars().all()

    queued_result = await db.execute(
        select(ChangeRequest).where(
            ChangeRequest.status == ChangeRequestStatus.queued_for_maintenance
        )
    )
    queued = queued_result.scalars().all()

    for cr in queued:
        # Gather tags for all target assets
        asset_tags: set[str] = set()
        if cr.target_asset_ids:
            assets_result = await db.execute(
                select(Asset).where(Asset.id.in_(cr.target_asset_ids))
            )
            assets = assets_result.scalars().all()
            for asset in assets:
                if asset.tags:
                    asset_tags.update(asset.tags if isinstance(asset.tags, list) else [])

        if is_window_open_for_org(windows, asset_tags, now):
            cr.status = ChangeRequestStatus.approved
            await db.commit()
            logger.info(f"Promoted change_request {cr.id} from queued_for_maintenance to approved")
            # Trigger execution asynchronously (fire-and-forget)
            try:
                from app.workflows.execute_change_workflow import execute_change_workflow
                import asyncio
                asyncio.create_task(execute_change_workflow(str(cr.id), _db_factory))
            except Exception as exc:
                logger.error(f"Failed to trigger execution for {cr.id}: {exc}")


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
