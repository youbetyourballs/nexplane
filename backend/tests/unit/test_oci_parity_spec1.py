# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import sys
from unittest.mock import MagicMock, patch, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {"user": "u", "key_content": "k", "fingerprint": "f",
                               "tenancy": "t", "region": "us-ashburn-1", "private_key": "pk"}
    return c


def _empty_connector():
    c = MagicMock()
    c.credentials = {}
    return c


# ---------------------------------------------------------------------------
# Task 1: OCIR
# ---------------------------------------------------------------------------

class TestCreateOcirRepository:
    def test_mock_mode(self):
        from app.connectors.executors.oci.create_ocir_repository import execute
        result = asyncio.run(execute(
            {"compartment_id": "ocid1.compartment.x", "display_name": "test-repo", "is_public": False},
            [], _empty_connector()
        ))
        assert result["mock"] is True
        assert "repository_id" in result

    def test_rollback_capability_constant(self):
        import app.connectors.executors.oci.create_ocir_repository as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_execute_calls_create_repository(self):
        from app.connectors.executors.oci.create_ocir_repository import execute
        fake_repo = MagicMock()
        fake_repo.id = "ocid1.containerrepo.x"
        fake_client = MagicMock()
        fake_client.create_container_repository.return_value = MagicMock(data=fake_repo)
        fake_oci = MagicMock()
        with patch("app.connectors.executors.oci.create_ocir_repository.get_artifacts_client", return_value=fake_client), \
             patch.dict(sys.modules, {"oci": fake_oci}):
            result = asyncio.run(execute(
                {"compartment_id": "ocid1.compartment.x", "display_name": "test-repo", "is_public": False},
                [], _connector()
            ))
        assert result["repository_id"] == "ocid1.containerrepo.x"
        fake_client.create_container_repository.assert_called_once()

    def test_rollback_deletes_repository(self):
        from app.connectors.executors.oci.create_ocir_repository import rollback
        fake_client = MagicMock()
        fake_client.delete_container_repository.return_value = None
        with patch("app.connectors.executors.oci.create_ocir_repository.get_artifacts_client", return_value=fake_client):
            result = asyncio.run(rollback(
                {},
                {"repository_id": "ocid1.containerrepo.x"},
                _connector()
            ))
        fake_client.delete_container_repository.assert_called_once_with(repository_id="ocid1.containerrepo.x")
        assert result["status"] == "DELETED"


class TestDeleteOcirRepository:
    def test_mock_mode(self):
        from app.connectors.executors.oci.delete_ocir_repository import execute
        result = asyncio.run(execute({"repository_id": "ocid1.containerrepo.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_rollback_capability_constant(self):
        import app.connectors.executors.oci.delete_ocir_repository as m
        assert m.ROLLBACK_CAPABILITY == "full"

    def test_execute_captures_state_before_delete(self):
        from app.connectors.executors.oci.delete_ocir_repository import execute
        fake_repo = MagicMock()
        fake_repo.display_name = "test-repo"
        fake_repo.compartment_id = "ocid1.compartment.x"
        fake_repo.is_immutable = False
        fake_repo.defined_tags = {}
        fake_repo.freeform_tags = {}
        fake_client = MagicMock()
        fake_client.get_container_repository.return_value = MagicMock(data=fake_repo)
        fake_client.delete_container_repository.return_value = None
        call_order = []
        with patch("app.connectors.executors.oci.delete_ocir_repository.get_artifacts_client", return_value=fake_client), \
             patch("app.connectors.executors.oci.delete_ocir_repository.PreStateStore") as mock_store, \
             patch("app.connectors.executors.oci.delete_ocir_repository.AsyncSessionLocal") as mock_session:
            mock_db = AsyncMock()
            mock_db.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_store.capture = AsyncMock(side_effect=lambda *a, **kw: call_order.append("capture"))
            fake_client.delete_container_repository.side_effect = lambda **kw: call_order.append("delete")
            asyncio.run(execute(
                {"repository_id": "ocid1.containerrepo.x", "cr_id": "00000000-0000-0000-0000-000000000001",
                 "step_id": "step_0", "org_id": "00000000-0000-0000-0000-000000000002"},
                [], _connector()
            ))
        assert call_order.index("capture") < call_order.index("commit") < call_order.index("delete")


class TestDeleteOcirImage:
    def test_mock_mode(self):
        from app.connectors.executors.oci.delete_ocir_image import execute
        result = asyncio.run(execute({"image_id": "ocid1.containerimage.x"}, [], _empty_connector()))
        assert result["mock"] is True

    def test_rollback_capability_irreversible(self):
        import app.connectors.executors.oci.delete_ocir_image as m
        assert m.ROLLBACK_CAPABILITY == "irreversible"
        assert m.ROLLBACK_REASON

    def test_rollback_returns_false(self):
        from app.connectors.executors.oci.delete_ocir_image import rollback
        result = asyncio.run(rollback({}, {}, _connector()))
        assert result["rolled_back"] is False
