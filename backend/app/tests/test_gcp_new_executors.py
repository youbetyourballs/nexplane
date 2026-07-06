# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import pytest


class _FakeConnector:
    credentials = {}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_create_bucket_no_creds_returns_action():
    from app.connectors.executors.gcp.create_bucket import execute
    result = _run(execute({"bucket_name": "smoke-test-bucket", "location": "US"}, [], _FakeConnector()))
    assert result["action"] == "create_bucket"
    assert result["bucket_name"] == "smoke-test-bucket"


def test_delete_bucket_no_creds_returns_action():
    from app.connectors.executors.gcp.delete_bucket import execute
    result = _run(execute({"bucket_name": "smoke-test-bucket"}, [], _FakeConnector()))
    assert result["action"] == "delete_bucket"
    assert result["deleted"] is True


def test_create_bucket_rollback_calls_delete():
    from app.connectors.executors.gcp.create_bucket import rollback
    result = _run(rollback(
        {"bucket_name": "smoke-test-bucket"},
        {"bucket_name": "smoke-test-bucket"},
        _FakeConnector(),
    ))
    assert result["action"] == "delete_bucket"
