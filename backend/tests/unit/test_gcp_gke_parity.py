# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import sys
import asyncio
from unittest.mock import MagicMock, patch


# Patch google SDKs before any import of the modules under test
_google_mock = MagicMock()
_container_v1_mock = MagicMock()
_oauth2_mock = MagicMock()
_google_auth_mock = MagicMock()
_google_auth_transport_mock = MagicMock()
_google_auth_transport_requests_mock = MagicMock()

sys.modules.setdefault("google", _google_mock)
sys.modules.setdefault("google.cloud", _google_mock.cloud)
sys.modules.setdefault("google.cloud.container_v1", _container_v1_mock)
sys.modules.setdefault("google.oauth2", _oauth2_mock)
sys.modules.setdefault("google.oauth2.service_account", _oauth2_mock.service_account)
sys.modules.setdefault("google.auth", _google_auth_mock)
sys.modules.setdefault("google.auth.transport", _google_auth_transport_mock)
sys.modules.setdefault("google.auth.transport.requests", _google_auth_transport_requests_mock)


def _make_creds():
    import json
    return {
        "service_account_key_json": json.dumps({
            "type": "service_account",
            "project_id": "test-project",
            "private_key_id": "key1",
            "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4PAtEsHAFbFPaJ7Kk6SJQNQLQ==\n-----END RSA PRIVATE KEY-----\n",
            "client_email": "nexplane@test-project.iam.gserviceaccount.com",
            "client_id": "123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }),
        "project_id": "test-project",
    }


class TestGkeClientFactory:
    def test_get_container_client_returns_client(self):
        from app.connectors.executors.gcp._client import get_container_client
        creds = _make_creds()
        client = get_container_client(creds)
        assert client is not None

    def test_poll_gke_operation_done_returns(self):
        from app.connectors.executors.gcp._gke_helpers import poll_gke_operation
        from unittest.mock import MagicMock
        client = MagicMock()
        done_op = MagicMock()
        done_op.status = _container_v1_mock.Operation.Status.DONE
        done_op.status_message = ""
        client.get_operation.return_value = done_op
        asyncio.get_event_loop().run_until_complete(
            poll_gke_operation(client, "projects/p/locations/l/operations/op1", 60)
        )

    def test_poll_gke_operation_failure_raises(self):
        import pytest
        from app.connectors.executors.gcp._gke_helpers import poll_gke_operation
        client = MagicMock()
        failed_op = MagicMock()
        failed_op.status = _container_v1_mock.Operation.Status.DONE
        failed_op.status_message = "quota exceeded"
        client.get_operation.return_value = failed_op
        with pytest.raises(RuntimeError, match="GKE operation failed"):
            asyncio.get_event_loop().run_until_complete(
                poll_gke_operation(client, "projects/p/locations/l/operations/op1", 60)
            )

    def test_poll_gke_operation_timeout_raises(self):
        import pytest
        from app.connectors.executors.gcp._gke_helpers import poll_gke_operation
        client = MagicMock()
        pending_op = MagicMock()
        pending_op.status = MagicMock()  # not DONE
        client.get_operation.return_value = pending_op
        # Force status != DONE by making the comparison always False
        _container_v1_mock.Operation.Status.DONE = object()
        with pytest.raises(TimeoutError):
            asyncio.get_event_loop().run_until_complete(
                poll_gke_operation(client, "projects/p/locations/l/operations/op1", 0)
            )
