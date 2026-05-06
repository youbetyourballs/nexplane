import uuid
import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import get_db, AsyncSessionLocal
from app.routers import auth, assets, connectors, change_requests, audit, projects
from app.routers import settings as settings_router
from app.routers import agent as agent_router
from app.routers import vulnerability as vulnerability_router
from app.routers import compliance as compliance_router
from app.routers import runbooks as runbooks_router
from app.routers import review_campaigns as review_campaigns_router
from app.routers import maintenance_windows as maintenance_windows_router
from app.routers import ir as ir_router
from app.routers import smoke_tests as smoke_tests_router
from app.routers import current_user
from app.routers.audit import list_cr_audit_events
from app.services import scheduler_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.connectors.catalog_service import init_catalog_service
    import pathlib
    init_catalog_service(pathlib.Path(__file__).parent / "connectors" / "catalog")
    scheduler_service.init_scheduler(lambda: AsyncSessionLocal())
    await scheduler_service.start()
    yield
    scheduler_service.stop()


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
app.include_router(vulnerability_router.router)
app.include_router(compliance_router.router)
app.include_router(runbooks_router.router)
app.include_router(runbooks_router.execution_router)
app.include_router(review_campaigns_router.router)
app.include_router(maintenance_windows_router.router)
app.include_router(ir_router.router)
app.include_router(smoke_tests_router.router)


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
