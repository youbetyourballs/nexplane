import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


def test_timeline_router_exists():
    from app.routers.asset_timeline import router
    # Check the /assets/{asset_id}/timeline route exists
    routes = [r.path for r in router.routes]
    assert any("timeline" in p for p in routes)
