# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Pytest configuration for smoke tests.
Adds the smoke directory to sys.path for relative imports of smoke_helpers.
"""
import sys
from pathlib import Path

# Add the smoke test directory to sys.path so smoke_helpers can be imported
smoke_dir = Path(__file__).parent
if str(smoke_dir) not in sys.path:
    sys.path.insert(0, str(smoke_dir))


def pytest_configure(config):
    """
    Patch the database engine to use NullPool for smoke tests.
    This prevents asyncpg from caching connections across event loops,
    which causes 'Future attached to a different loop' errors.
    """
    try:
        import app.database as _db
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        _engine = create_async_engine(
            _db.engine.url,
            echo=False,
            poolclass=NullPool,
        )
        _db.engine = _engine
        _db.AsyncSessionLocal = async_sessionmaker(
            _engine, class_=AsyncSession, expire_on_commit=False
        )
    except Exception:
        pass  # If app isn't importable (e.g., outside container), skip silently
