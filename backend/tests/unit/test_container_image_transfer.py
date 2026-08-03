# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

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
