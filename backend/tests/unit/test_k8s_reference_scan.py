# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for Kubernetes reference scan executors."""

import sys
from unittest.mock import MagicMock

# Stub out the kubernetes package before any app imports touch it.
# The unit tests patch get_k8s_client entirely, so no real k8s client is needed.
_k8s_stub = MagicMock()
sys.modules.setdefault("kubernetes", _k8s_stub)
sys.modules.setdefault("kubernetes.client", _k8s_stub.client)
sys.modules.setdefault("kubernetes.config", _k8s_stub.config)
sys.modules.setdefault("yaml", sys.modules.get("yaml", MagicMock()))

import pytest
from unittest.mock import AsyncMock, patch, PropertyMock


def _make_connector():
    connector = MagicMock()
    connector.credentials = {"kubeconfig": "dGVzdA=="}  # base64 "test"
    return connector


def _make_cr(search_terms, namespaces=None):
    cr = MagicMock()
    params = {"search_terms": search_terms}
    if namespaces:
        params["namespaces"] = namespaces
    cr.parameters = params
    return cr


def _make_db():
    return AsyncMock()


# ---------------------------------------------------------------------------
# scan_configmaps
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _make_configmap(name="app-config", namespace="production", data=_SENTINEL):
    cm = MagicMock()
    cm.metadata.name = name
    cm.metadata.namespace = namespace
    cm.data = {"DATABASE_URL": "postgres://old-db.internal/app"} if data is _SENTINEL else data
    return cm


def _make_core_api_with_configmaps(configmaps):
    api = MagicMock()
    api.list_config_map_for_all_namespaces.return_value.items = configmaps
    return api


@pytest.mark.asyncio
async def test_scan_configmaps_finds_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_configmaps

    cm = _make_configmap(data={"DATABASE_URL": "postgres://old-db.internal/app"})
    mock_clients = {"core": _make_core_api_with_configmaps([cm]), "apps": MagicMock(), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_configmaps(_make_cr(["old-db.internal"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    assert len(result["hits"]) == 1
    hit = result["hits"][0]
    assert hit["surface"] == "k8s_configmap"
    assert hit["matched_term"] == "old-db.internal"
    assert hit["consumer_identity"]["stable_id"] == "production/ConfigMap/app-config"
    assert "DATABASE_URL=postgres://old-db.internal/app" in hit["snippet"]


@pytest.mark.asyncio
async def test_scan_configmaps_no_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_configmaps

    cm = _make_configmap(data={"DATABASE_URL": "postgres://new-db.internal/app"})
    mock_clients = {"core": _make_core_api_with_configmaps([cm]), "apps": MagicMock(), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_configmaps(_make_cr(["old-db.internal"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    assert len(result["hits"]) == 0


@pytest.mark.asyncio
async def test_scan_configmaps_empty_data():
    """ConfigMap with no data should not crash."""
    from app.connectors.executors.kubernetes.reference_scan import scan_configmaps

    cm = _make_configmap(data=None)
    mock_clients = {"core": _make_core_api_with_configmaps([cm]), "apps": MagicMock(), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_configmaps(_make_cr(["old-db.internal"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    assert len(result["hits"]) == 0


# ---------------------------------------------------------------------------
# scan_secrets_metadata
# ---------------------------------------------------------------------------

def _make_secret(name="db-credentials", namespace="production", labels=None):
    secret = MagicMock()
    secret.metadata.name = name
    secret.metadata.namespace = namespace
    secret.metadata.labels = labels or {}
    # data is intentionally never accessed in scan_secrets_metadata
    return secret


def _make_core_api_with_secrets(secrets):
    api = MagicMock()
    api.list_secret_for_all_namespaces.return_value.items = secrets
    return api


@pytest.mark.asyncio
async def test_scan_secrets_metadata_finds_match_in_name():
    from app.connectors.executors.kubernetes.reference_scan import scan_secrets_metadata

    secret = _make_secret(name="old-db-credentials")
    mock_clients = {"core": _make_core_api_with_secrets([secret]), "apps": MagicMock(), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_secrets_metadata(_make_cr(["old-db"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    assert len(result["hits"]) >= 1
    hit = result["hits"][0]
    assert hit["surface"] == "k8s_secret_metadata"
    assert hit["matched_term"] == "old-db"
    assert hit["consumer_identity"]["stable_id"] == "production/Secret/old-db-credentials"


@pytest.mark.asyncio
async def test_scan_secrets_metadata_no_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_secrets_metadata

    secret = _make_secret(name="new-db-credentials")
    mock_clients = {"core": _make_core_api_with_secrets([secret]), "apps": MagicMock(), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_secrets_metadata(_make_cr(["old-db"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    assert len(result["hits"]) == 0


@pytest.mark.asyncio
async def test_scan_secrets_metadata_never_reads_data():
    """Verify secret.data is never accessed."""
    from app.connectors.executors.kubernetes.reference_scan import scan_secrets_metadata

    secret = _make_secret(name="unrelated-secret")
    # Make .data raise an AssertionError if accessed, ensuring scan_secrets_metadata never reads it
    type(secret).data = PropertyMock(side_effect=AssertionError("scan_secrets_metadata must not access .data"))
    mock_clients = {"core": _make_core_api_with_secrets([secret]), "apps": MagicMock(), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_secrets_metadata(_make_cr(["unrelated"]), _make_connector(), _make_db())

    # If we reach this point without an AssertionError, .data was never accessed
    assert result["scanned"] == 1


# ---------------------------------------------------------------------------
# scan_deployment_env
# ---------------------------------------------------------------------------

def _make_deployment(name="web", namespace="default", containers=None):
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = namespace
    if containers is None:
        container = MagicMock()
        container.name = "app"
        container.image = "myapp:latest"
        env_var = MagicMock()
        env_var.name = "DATABASE_URL"
        env_var.value = "postgres://old-db.internal/app"
        container.env = [env_var]
        containers = [container]
    dep.spec.template.spec.containers = containers
    return dep


def _make_apps_api_with_deployments(deployments, statefulsets=None):
    api = MagicMock()
    api.list_deployment_for_all_namespaces.return_value.items = deployments
    api.list_stateful_set_for_all_namespaces.return_value.items = statefulsets or []
    return api


@pytest.mark.asyncio
async def test_scan_deployment_env_finds_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_deployment_env

    dep = _make_deployment()
    mock_clients = {"core": MagicMock(), "apps": _make_apps_api_with_deployments([dep]), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_deployment_env(_make_cr(["old-db.internal"]), _make_connector(), _make_db())

    assert result["scanned"] >= 1
    assert len(result["hits"]) >= 1
    hit = result["hits"][0]
    assert hit["surface"] == "k8s_deployment_spec"
    assert hit["matched_term"] == "old-db.internal"
    assert hit["consumer_identity"]["stable_id"] == "default/Deployment/web"


@pytest.mark.asyncio
async def test_scan_deployment_env_no_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_deployment_env

    container = MagicMock()
    container.name = "app"
    container.image = "myapp:latest"
    env_var = MagicMock()
    env_var.name = "DATABASE_URL"
    env_var.value = "postgres://new-db.internal/app"
    container.env = [env_var]
    dep = _make_deployment(containers=[container])

    mock_clients = {"core": MagicMock(), "apps": _make_apps_api_with_deployments([dep]), "networking": MagicMock()}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_deployment_env(_make_cr(["old-db.internal"]), _make_connector(), _make_db())

    env_hits = [h for h in result["hits"] if "DATABASE_URL" in h["snippet"]]
    assert len(env_hits) == 0


# ---------------------------------------------------------------------------
# scan_ingress_rules
# ---------------------------------------------------------------------------

def _make_ingress(name="web-ingress", namespace="default", host="old-service.internal", backend_svc="old-svc"):
    ingress = MagicMock()
    ingress.metadata.name = name
    ingress.metadata.namespace = namespace

    rule = MagicMock()
    rule.host = host

    path_obj = MagicMock()
    path_obj.path = "/"
    path_obj.backend.service.name = backend_svc

    rule.http.paths = [path_obj]
    ingress.spec.rules = [rule]
    return ingress


def _make_networking_api_with_ingresses(ingresses):
    api = MagicMock()
    api.list_ingress_for_all_namespaces.return_value.items = ingresses
    return api


@pytest.mark.asyncio
async def test_scan_ingress_rules_finds_host_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_ingress_rules

    ingress = _make_ingress(host="old-service.internal")
    mock_clients = {"core": MagicMock(), "apps": MagicMock(), "networking": _make_networking_api_with_ingresses([ingress])}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_ingress_rules(_make_cr(["old-service.internal"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    host_hits = [h for h in result["hits"] if "host=" in h["snippet"]]
    assert len(host_hits) >= 1
    assert host_hits[0]["surface"] == "k8s_ingress"
    assert host_hits[0]["matched_term"] == "old-service.internal"
    assert host_hits[0]["consumer_identity"]["stable_id"] == "default/Ingress/web-ingress"


@pytest.mark.asyncio
async def test_scan_ingress_rules_finds_backend_service_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_ingress_rules

    ingress = _make_ingress(host="unrelated.host", backend_svc="old-svc")
    mock_clients = {"core": MagicMock(), "apps": MagicMock(), "networking": _make_networking_api_with_ingresses([ingress])}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_ingress_rules(_make_cr(["old-svc"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    svc_hits = [h for h in result["hits"] if "backend.service.name" in h["snippet"]]
    assert len(svc_hits) >= 1
    assert svc_hits[0]["matched_term"] == "old-svc"


@pytest.mark.asyncio
async def test_scan_ingress_rules_no_match():
    from app.connectors.executors.kubernetes.reference_scan import scan_ingress_rules

    ingress = _make_ingress(host="new-service.internal", backend_svc="new-svc")
    mock_clients = {"core": MagicMock(), "apps": MagicMock(), "networking": _make_networking_api_with_ingresses([ingress])}

    with patch("app.connectors.executors.kubernetes.reference_scan.get_k8s_client", return_value=mock_clients):
        result = await scan_ingress_rules(_make_cr(["old-service.internal"]), _make_connector(), _make_db())

    assert result["scanned"] == 1
    assert len(result["hits"]) == 0
