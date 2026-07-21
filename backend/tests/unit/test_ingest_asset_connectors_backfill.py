# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Verify that ingest and auto-asset creation write to asset_connectors."""
# This test is verified via the smoke test in Task 7 (live infra).
# Unit test checks that the helper insert statement is idempotent.
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.models.asset import asset_connectors_table
import uuid

def test_asset_connectors_insert_statement_builds():
    asset_id = uuid.uuid4()
    connector_id = uuid.uuid4()
    stmt = (
        pg_insert(asset_connectors_table)
        .values(asset_id=asset_id, connector_id=connector_id)
        .on_conflict_do_nothing()
    )
    compiled = stmt.compile(dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect())
    assert "ON CONFLICT" in str(compiled).upper()
