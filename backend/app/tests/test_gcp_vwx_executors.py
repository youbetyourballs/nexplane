# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


class _FakeConnector:
    credentials = {}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --- DNS ---

def test_create_dns_zone_no_creds():
    from app.connectors.executors.gcp.create_dns_zone import execute
    result = _run(execute(
        {"zone_name": "smoke-zone", "dns_name": "smoke.example.", "description": "test"},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_dns_zone"
    assert result["zone_name"] == "smoke-zone"


def test_delete_dns_zone_no_creds():
    from app.connectors.executors.gcp.delete_dns_zone import execute
    result = _run(execute({"zone_name": "smoke-zone"}, [], _FakeConnector()))
    assert result["action"] == "delete_dns_zone"
    assert result["deleted"] is True


def test_create_dns_record_no_creds():
    from app.connectors.executors.gcp.create_dns_record import execute
    result = _run(execute(
        {"zone_name": "smoke-zone", "record_name": "test.smoke.example.", "record_type": "A",
         "ttl": 300, "rrdatas": ["1.2.3.4"]},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_dns_record"
    assert result["record_name"] == "test.smoke.example."


def test_delete_dns_record_no_creds():
    from app.connectors.executors.gcp.delete_dns_record import execute
    result = _run(execute(
        {"zone_name": "smoke-zone", "record_name": "test.smoke.example.", "record_type": "A"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_dns_record"
    assert result["deleted"] is True


# --- Cloud SQL ---

def test_create_cloudsql_instance_no_creds():
    from app.connectors.executors.gcp.create_cloudsql_instance import execute
    result = _run(execute(
        {"instance_name": "smoke-sql", "database_version": "POSTGRES_14",
         "tier": "db-f1-micro", "region": "us-central1"},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_cloudsql_instance"
    assert result["instance_name"] == "smoke-sql"


def test_delete_cloudsql_instance_no_creds():
    from app.connectors.executors.gcp.delete_cloudsql_instance import execute
    result = _run(execute({"instance_name": "smoke-sql"}, [], _FakeConnector()))
    assert result["action"] == "delete_cloudsql_instance"
    assert result["deleted"] is True


def test_create_cloudsql_backup_no_creds():
    from app.connectors.executors.gcp.create_cloudsql_backup import execute
    result = _run(execute({"instance_name": "smoke-sql"}, [], _FakeConnector()))
    assert result["action"] == "create_cloudsql_backup"
    assert result["instance_name"] == "smoke-sql"


# --- Cloud Monitoring ---

def test_create_alert_policy_no_creds():
    from app.connectors.executors.gcp.create_alert_policy import execute
    result = _run(execute(
        {"display_name": "smoke-alert", "condition_threshold": 0.9, "duration_seconds": 60},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_alert_policy"
    assert result["display_name"] == "smoke-alert"


def test_delete_alert_policy_no_creds():
    from app.connectors.executors.gcp.delete_alert_policy import execute
    result = _run(execute(
        {"policy_name": "projects/p/alertPolicies/123"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_alert_policy"
    assert result["deleted"] is True


def test_create_uptime_check_no_creds():
    from app.connectors.executors.gcp.create_uptime_check import execute
    result = _run(execute(
        {"display_name": "smoke-uptime", "host": "google.com", "path": "/", "period_seconds": 60},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_uptime_check"
    assert result["display_name"] == "smoke-uptime"


def test_delete_uptime_check_no_creds():
    from app.connectors.executors.gcp.delete_uptime_check import execute
    result = _run(execute(
        {"check_id": "projects/p/uptimeCheckConfigs/abc"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_uptime_check"
    assert result["deleted"] is True
