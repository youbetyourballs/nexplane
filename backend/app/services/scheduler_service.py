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
