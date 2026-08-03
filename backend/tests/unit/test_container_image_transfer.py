# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_catalog_entry_loads():
    import json, pathlib
    p = pathlib.Path(__file__).parent.parent.parent / "app" / "connectors" / "catalog" / "container_registry.json"
    assert p.exists(), f"Catalog file not found at {p}"
    data = json.loads(p.read_text())
    assert data["connector_type"] == "container_registry"
    actions = {a["action_id"]: a for a in data.get("actions", [])}
    assert "container_image_transfer" in actions
    assert actions["container_image_transfer"]["executor"] == "container_registry.container_image_transfer"


def test_change_type_enum_value():
    from app.models.change_request import ChangeType
    assert ChangeType.container_image_transfer.value == "container_image_transfer"


from app.connectors.executors._registry_client import get_registry_hostname

def test_get_registry_hostname_ecr():
    creds = {"account_id": "123456789012", "region": "us-east-1"}
    assert get_registry_hostname("aws", creds) == "123456789012.dkr.ecr.us-east-1.amazonaws.com"

def test_get_registry_hostname_acr():
    creds = {"registry_name": "myregistry"}
    assert get_registry_hostname("azure", creds) == "myregistry.azurecr.io"

def test_get_registry_hostname_gcr():
    creds = {"project_id": "myproject", "region": "us"}
    assert get_registry_hostname("gcp", creds) == "us-docker.pkg.dev/myproject"

def test_get_registry_hostname_ocir():
    creds = {"region": "us-ashburn-1", "tenancy_namespace": "mynamespace"}
    assert get_registry_hostname("oci", creds) == "iad.ocir.io/mynamespace"


def test_check_tag_exists_ecr_found(mocker):
    from app.connectors.executors._registry_client import check_tag_exists
    mock_ecr = mocker.MagicMock()
    mock_ecr.describe_images.return_value = {"imageDetails": [{"imageDigest": "sha256:abc"}]}
    mocker.patch("boto3.client", return_value=mock_ecr)
    creds = {"account_id": "123", "region": "us-east-1",
             "access_key_id": "k", "secret_access_key": "s"}
    assert check_tag_exists("aws", creds, "myrepo", "v1") is True

def test_check_tag_exists_ecr_missing(mocker):
    from app.connectors.executors._registry_client import check_tag_exists

    class ImageNotFoundException(Exception):
        pass

    mock_ecr = mocker.MagicMock()
    mock_ecr.describe_images.side_effect = ImageNotFoundException("not found")
    mocker.patch("boto3.client", return_value=mock_ecr)
    creds = {"account_id": "123", "region": "us-east-1",
             "access_key_id": "k", "secret_access_key": "s"}
    assert check_tag_exists("aws", creds, "myrepo", "v1") is False


import pytest


@pytest.mark.asyncio
async def test_execute_mock_returns_expected_shape():
    from app.connectors.executors.container_image_transfer import execute

    class MockConnector:
        credentials = {}

    result = await execute({
        "source_image": "myapp/api:v1.2.3",
        "dest_repo": "nexplane/myapp/api",
        "overwrite_existing": False,
    }, [], MockConnector())
    assert result["mock"] is True
    assert result["promote_to"] == "container_image_transfer"
    assert "rollback_data" in result


@pytest.mark.asyncio
async def test_preflight_fails_when_source_missing(mocker):
    from app.connectors.executors.container_image_transfer import _preflight
    mocker.patch(
        "app.connectors.executors._registry_client.check_tag_exists",
        return_value=False,
    )
    mocker.patch(
        "app.connectors.executors._registry_client.get_registry_hostname",
        return_value="123.dkr.ecr.us-east-1.amazonaws.com",
    )
    result = await _preflight("aws", {}, "myrepo:v1",
                               "oci", {}, "dest/repo", False)
    assert result["status"] == "failed"
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_preflight_fails_when_dest_exists_no_overwrite(mocker):
    from app.connectors.executors.container_image_transfer import _preflight
    mocker.patch(
        "app.connectors.executors._registry_client.check_tag_exists",
        side_effect=[True, True],
    )
    mocker.patch(
        "app.connectors.executors._registry_client.get_registry_hostname",
        return_value="host",
    )
    result = await _preflight("aws", {}, "myrepo:v1",
                               "oci", {}, "dest/repo", False)
    assert result["status"] == "failed"
    assert "overwrite_existing" in result["error"]


@pytest.mark.asyncio
async def test_rollback_net_new_tag(mocker):
    from app.connectors.executors.container_image_transfer import rollback
    mocker.patch(
        "app.connectors.executors.container_image_transfer._load_creds_by_connector_id",
        return_value=("oci", {"tenancy_namespace": "ns", "username": "u", "auth_token": "t",
                               "region": "us-ashburn-1"}),
    )
    mock_delete = mocker.patch("app.connectors.executors._registry_client.delete_tag")
    execution_result = {
        "rollback_data": {
            "snapshot": {"exists": False, "digest": None},
            "dest_image": "nexplane/smoke/alpine:3.19",
            "dst_connector_type": "oci",
        }
    }
    result = await rollback(
        {"dest_connector_id": "some-uuid"},
        execution_result,
        None,
    )
    assert result["rolled_back"] is True
    assert result["note"] == "deleted_net_new_tag"
    mock_delete.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_restore_original_digest(mocker):
    from app.connectors.executors.container_image_transfer import rollback
    mocker.patch(
        "app.connectors.executors.container_image_transfer._load_creds_by_connector_id",
        return_value=("oci", {}),
    )
    mocker.patch("app.connectors.executors._registry_client.restore_tag_by_digest", return_value=True)
    execution_result = {
        "rollback_data": {
            "snapshot": {"exists": True, "digest": "sha256:abc"},
            "dest_image": "nexplane/smoke/alpine:3.19",
            "dst_connector_type": "oci",
        }
    }
    result = await rollback({"dest_connector_id": "some-uuid"}, execution_result, None)
    assert result["rolled_back"] is True
    assert result["note"] == "restored_original_digest"
