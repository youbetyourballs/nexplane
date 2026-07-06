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


def test_create_service_account_no_creds():
    from app.connectors.executors.gcp.create_service_account import execute
    result = _run(execute(
        {"account_id": "test-sa", "display_name": "Test SA"},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_service_account"
    assert "email" in result


def test_delete_service_account_no_creds():
    from app.connectors.executors.gcp.delete_service_account import execute
    result = _run(execute(
        {"email": "test@project.iam.gserviceaccount.com"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_service_account"
    assert result["deleted"] is True


def test_add_iam_binding_no_creds():
    from app.connectors.executors.gcp.add_iam_binding import execute
    result = _run(execute(
        {"role": "roles/viewer", "member": "serviceAccount:test@project.iam.gserviceaccount.com"},
        [], _FakeConnector()
    ))
    assert result["action"] == "add_iam_binding"
    assert result["role"] == "roles/viewer"


def test_remove_iam_binding_no_creds():
    from app.connectors.executors.gcp.remove_iam_binding import execute
    result = _run(execute(
        {"role": "roles/viewer", "member": "serviceAccount:test@project.iam.gserviceaccount.com"},
        [], _FakeConnector()
    ))
    assert result["action"] == "remove_iam_binding"
    assert result["role"] == "roles/viewer"
