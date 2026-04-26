import uuid
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import get_db
from app.routers import auth, assets, connectors, change_requests, audit
from app.routers import current_user
from app.routers.audit import list_cr_audit_events

app = FastAPI(
    title="Nexplane API",
    description="Safe execution layer for security-driven infrastructure change",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(connectors.router)
app.include_router(change_requests.router)
app.include_router(audit.router)


@app.get("/change-requests/{cr_id}/audit-events", tags=["Audit"])
async def cr_audit_events(
    cr_id: uuid.UUID,
    user=Depends(current_user),
    db=Depends(get_db),
):
    return await list_cr_audit_events(cr_id, user, db)


@app.on_event("startup")
async def startup_event():
    from app.connectors.catalog_service import init_catalog_service
    import pathlib
    init_catalog_service(pathlib.Path(__file__).parent / "connectors" / "catalog")


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "service": "nexplane"}
