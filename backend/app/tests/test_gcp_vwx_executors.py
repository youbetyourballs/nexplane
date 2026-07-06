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
