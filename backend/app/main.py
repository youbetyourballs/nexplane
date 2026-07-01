# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import get_db, AsyncSessionLocal
from app.routers import auth, assets, connectors, change_requests, audit, projects
from app.routers.policy_generate import router as policy_generate_router
from app.routers.drift_alerts import router as drift_alerts_router
from app.routers import settings as settings_router
from app.routers import agent as agent_router
from app.routers import agent_tunnel_admin as agent_tunnel_admin_router
from app.routers import vulnerability as vulnerability_router
from app.routers import compliance as compliance_router
from app.routers import runbooks as runbooks_router
from app.routers import review_campaigns as review_campaigns_router
from app.routers import maintenance_windows as maintenance_windows_router
from app.routers import ir as ir_router
from app.routers import smoke_tests as smoke_tests_router
from app.routers import access_reviews as access_reviews_router
from app.routers import notifications as notifications_router
from app.routers import current_user
from app.routers.audit import list_cr_audit_events
from app.routers.asset_timeline import router as asset_timeline_router
from app.routers.asset_graph import router as asset_graph_router
from app.routers.onboarding import router as onboarding_router
from app.routers.credential_discovery import router as credential_discovery_router
from app.routers.smoke_test_runs import router as smoke_test_runs_router
from app.routers.identity import router as identity_router
from app.routers.api_tokens import router as api_tokens_router
from app.routers import recurring_jobs as recurring_jobs_router
from app.routers import backup as backup_router
from app.routers import security_policy as security_policy_router
from app.routers import cr_manifest as cr_manifest_router
from app.routers.setup import router as setup_router
from app.routers.identity_providers import router as identity_providers_router
from app.routers.org_auth_mode import router as org_auth_mode_router
from app.routers.oidc import router as oidc_router
from app.routers.infrastructure_memory import router as infrastructure_memory_router
from app.routers.impact_simulation import router as impact_simulation_router
from app.routers.recommendations import router as recommendations_router
from app.middleware.setup_guard import SetupGuardMiddleware
from app.mcp_server import create_mcp_app
from app.services import scheduler_service
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.workers.escalation_worker import check_emergency_escalations
from app.workers.soak_timer_worker import check_soak_timers
from app.workers.scheduled_cr_worker import execute_scheduled_crs
from app.workers.drift_check_worker import check_policy_drift
from app.workers.credential_expiry_worker import check_credential_expiry
from app.services.runbook_executor import tick_all_executions as _tick_runbooks
from app.services.version_poller import poll_version as _poll_version
from app.routers.version import router as version_router
from app.services.upgrade_verify import run_startup_verify as _run_upgrade_verify
from app.routers import catalog as catalog_router

_escalation_scheduler: AsyncIOScheduler | None = None
_socks_server = None  # app.tunnel.socks.SocksServer, started when TUNNEL_SOCKS_ENABLED


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _escalation_scheduler
    from app.connectors.catalog_service import init_catalog_service
    commercial_path = (
        pathlib.Path(settings.NEXPLANE_COMMERCIAL_CATALOG_PATH)
        if settings.NEXPLANE_COMMERCIAL_CATALOG_PATH
        else None
    )
    init_catalog_service(
        pathlib.Path(__file__).parent / "connectors" / "catalog",
        commercial_catalog_dir=commercial_path,
    )
    from app.services.manifest_builder import build_manifest
    build_manifest()
    scheduler_service.init_scheduler(lambda: AsyncSessionLocal())
    await scheduler_service.start()
    _escalation_scheduler = AsyncIOScheduler()
    _escalation_scheduler.add_job(check_emergency_escalations, "interval", minutes=5)
    _escalation_scheduler.add_job(check_soak_timers, "interval", minutes=30)
    _escalation_scheduler.add_job(execute_scheduled_crs, "interval", minutes=1)
    _escalation_scheduler.add_job(check_policy_drift, "interval", hours=24)
    _escalation_scheduler.add_job(check_credential_expiry, "cron", hour=6, minute=0)
    _escalation_scheduler.add_job(_poll_version, "interval", hours=6, id="version_poller", replace_existing=True, max_instances=1)
    async def _tick_runbooks_job():
        await _tick_runbooks(AsyncSessionLocal)

    _escalation_scheduler.add_job(
        _tick_runbooks_job,
        "interval",
        seconds=30,
        id="runbook_executor_tick",
        replace_existing=True,
        max_instances=1,
    )

    async def _tick_scheduled_runbooks_job():
        from app.services.runbook_service import tick_scheduled_runbooks
        await tick_scheduled_runbooks(AsyncSessionLocal)

    _escalation_scheduler.add_job(
        _tick_scheduled_runbooks_job,
        "interval",
        minutes=1,
        id="scheduled_runbook_trigger",
        replace_existing=True,
        max_instances=1,
    )
    _escalation_scheduler.start()
    # Scrub orphaned CRs — any CR still in-flight when the backend
    # restarted will never complete; mark them failed now so the
    # dashboard doesn't show phantom "executing" entries.
    await _scrub_orphaned_crs()
    from app.services.project_rollback_service import resume_interrupted as _resume_rollbacks
    await _resume_rollbacks()
    await _run_upgrade_verify(AsyncSessionLocal)
    await _start_socks_proxy()
    yield
    await _stop_socks_proxy()
    scheduler_service.stop()
    if _escalation_scheduler and _escalation_scheduler.running:
        _escalation_scheduler.shutdown(wait=False)


async def _start_socks_proxy() -> None:
    """Start the reverse-tunnel SOCKS5 consumption proxy if enabled.

    Off by default; when on, any backend component / connector reaches an
    agent's network by pointing a SOCKS5 client at this proxy (username =
    agent id). The TunnelManager enforces each agent's allowlist per dial.
    """
    global _socks_server
    if not settings.TUNNEL_SOCKS_ENABLED:
        return
    import logging
    from app.tunnel.manager import get_manager
    from app.tunnel.socks import SocksServer

    _socks_server = SocksServer(
        get_manager(),
        host=settings.TUNNEL_SOCKS_HOST,
        port=settings.TUNNEL_SOCKS_PORT,
        auth_token=settings.TUNNEL_SOCKS_TOKEN,
    )
    host, port = await _socks_server.start()
    logging.getLogger(__name__).info("Reverse-tunnel SOCKS5 proxy listening on %s:%d", host, port)


async def _stop_socks_proxy() -> None:
    global _socks_server
    if _socks_server is not None:
        await _socks_server.stop()
        _socks_server = None


async def _scrub_orphaned_crs() -> None:
    """Mark any executing/verifying CRs as failed on startup.

    These CRs were mid-flight when the backend process exited. The
    workflow engine has no context to resume them, so they are
    permanently stuck. Marking them failed on startup keeps the
    dashboard accurate and prevents the UI from showing phantom activity.
    """
    import logging
    from sqlalchemy import update
    from app.models.change_request import ChangeRequest, ChangeRequestStatus
    from datetime import datetime, timezone

    _log = logging.getLogger(__name__)
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(ChangeRequest)
                .where(ChangeRequest.status.in_([
                    ChangeRequestStatus.executing,
                    ChangeRequestStatus.verifying,
                ]))
                .values(
                    status=ChangeRequestStatus.failed,
                    updated_at=datetime.now(timezone.utc),
                )
                .returning(ChangeRequest.id)
            )
            orphaned = result.fetchall()
            await db.commit()
            if orphaned:
                _log.warning(
                    "Scrubbed %d orphaned CR(s) (executing/verifying at startup): %s",
                    len(orphaned),
                    [str(r[0]) for r in orphaned],
                )
    except Exception as exc:
        logging.getLogger(__name__).error("Failed to scrub orphaned CRs: %s", exc)


app = FastAPI(
    title="Nexplane API",
    description="Safe execution layer for security-driven infrastructure change",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SetupGuardMiddleware)

_downloads_dir = pathlib.Path("/opt/nexplane-downloads")
if _downloads_dir.exists():
    app.mount("/downloads", StaticFiles(directory=str(_downloads_dir), html=False), name="downloads")

app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(connectors.router)
app.include_router(change_requests.router)
app.include_router(audit.router)
app.include_router(projects.router)
app.include_router(settings_router.router)
app.include_router(agent_router.router)
app.include_router(agent_tunnel_admin_router.router)
app.include_router(vulnerability_router.router)
app.include_router(compliance_router.router)
app.include_router(runbooks_router.router)
app.include_router(runbooks_router.execution_router)
app.include_router(review_campaigns_router.router)
app.include_router(maintenance_windows_router.router)
app.include_router(ir_router.router)
app.include_router(smoke_tests_router.router)
app.include_router(access_reviews_router.router)
app.include_router(notifications_router.router)
app.include_router(asset_timeline_router)
app.include_router(asset_graph_router)
app.include_router(policy_generate_router)
app.include_router(drift_alerts_router)
app.include_router(onboarding_router)
app.include_router(credential_discovery_router)
app.include_router(smoke_test_runs_router)
app.include_router(identity_router)
app.include_router(api_tokens_router)
app.include_router(recurring_jobs_router.router)
app.include_router(backup_router.router)
app.include_router(security_policy_router.router)
app.include_router(cr_manifest_router.router)
app.include_router(setup_router)
app.include_router(identity_providers_router)
app.include_router(org_auth_mode_router)
app.include_router(oidc_router)
app.include_router(infrastructure_memory_router)
app.include_router(impact_simulation_router)
app.include_router(recommendations_router)
app.include_router(version_router)
app.include_router(catalog_router.router)
import os as _os
if _os.getenv("DEMO_MODE") == "true":
    from app.routers.demo import router as demo_router
    app.include_router(demo_router, prefix="/demo", tags=["demo"])
app.mount("/mcp", create_mcp_app())


@app.get("/change-requests/{cr_id}/audit-events", tags=["Audit"])
async def cr_audit_events(
    cr_id: uuid.UUID,
    user=Depends(current_user),
    db=Depends(get_db),
):
    return await list_cr_audit_events(cr_id, user, db)



@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "nexplane"}
