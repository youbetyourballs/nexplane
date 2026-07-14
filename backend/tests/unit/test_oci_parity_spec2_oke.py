# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import sys
from unittest.mock import MagicMock, patch, AsyncMock


def _connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {
        "user": "u", "key_content": "k", "fingerprint": "f",
        "tenancy": "t", "region": "us-ashburn-1", "private_key": "pk",
    }
    return c


def _empty_connector():
    c = MagicMock()
    c.credentials = {}
    return c


class TestClientFactory:
    def test_get_container_engine_client_exists(self):
        from app.connectors.executors.oci._client import get_container_engine_client
        assert callable(get_container_engine_client)

    def test_poll_work_request_exists(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        assert callable(poll_work_request)

    def test_poll_work_request_returns_identifier_on_success(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        fake_resource = MagicMock()
        fake_resource.entity_type = "cluster"
        fake_resource.identifier = "ocid1.cluster.x"
        fake_wr = MagicMock()
        fake_wr.status = "SUCCEEDED"
        fake_wr.resources = [fake_resource]
        fake_client = MagicMock()
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        result = asyncio.run(poll_work_request(fake_client, "wr-1", "cluster", timeout=60))
        assert result == "ocid1.cluster.x"

    def test_poll_work_request_raises_on_failed(self):
        from app.connectors.executors.oci._oke_helpers import poll_work_request
        fake_wr = MagicMock()
        fake_wr.status = "FAILED"
        fake_wr.time_finished = "2026-01-01"
        fake_client = MagicMock()
        fake_client.get_work_request.return_value = MagicMock(data=fake_wr)
        try:
            asyncio.run(poll_work_request(fake_client, "wr-1", "cluster", timeout=60))
            assert False, "Expected RuntimeError"
        except RuntimeError:
            pass
