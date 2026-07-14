# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for kubernetes reference_update executors."""

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
from unittest.mock import AsyncMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cr(parameters):
    cr = MagicMock()
    cr.parameters = parameters
    cr.execution_result = None
    return cr


def _make_connector(creds=None):
    connector = MagicMock()
    connector.credentials = creds or {}
    return connector


def _make_mock_clients(core=None, apps=None, networking=None):
    return {
        "core": core or MagicMock(),
        "apps": apps or MagicMock(),
        "networking": networking or MagicMock(),
    }


# ---------------------------------------------------------------------------
# update_configmap_value
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_configmap_value_success():
    from app.connectors.executors.kubernetes.reference_update import update_configmap_value

    cr = _make_cr({
        "namespace": "production",
        "configmap_name": "app-config",
        "key": "DATABASE_URL",
        "old_value": "postgres://old-db.internal/app",
        "new_value": "postgres://new-db.internal/app",
    })
    connector = _make_connector()

    mock_cm = MagicMock()
    mock_cm.data = {"DATABASE_URL": "postgres://old-db.internal/app", "OTHER": "x"}
    core_api = MagicMock()
    core_api.read_namespaced_config_map.return_value = mock_cm
    mock_clients = _make_mock_clients(core=core_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await update_configmap_value(cr, connector, AsyncMock())

    assert result["status"] == "success"
    assert result["rollback_data"]["old_value"] == "postgres://old-db.internal/app"
    assert result["rollback_data"]["key"] == "DATABASE_URL"
    core_api.patch_namespaced_config_map.assert_called_once()
    patched_body = core_api.patch_namespaced_config_map.call_args[1]["body"]
    assert patched_body.data["DATABASE_URL"] == "postgres://new-db.internal/app"
    assert patched_body.data["OTHER"] == "x"


@pytest.mark.asyncio
async def test_update_configmap_value_old_value_mismatch():
    from app.connectors.executors.kubernetes.reference_update import update_configmap_value

    cr = _make_cr({
        "namespace": "production",
        "configmap_name": "app-config",
        "key": "DATABASE_URL",
        "old_value": "postgres://expected-old/app",
        "new_value": "postgres://new-db.internal/app",
    })
    connector = _make_connector()

    mock_cm = MagicMock()
    mock_cm.data = {"DATABASE_URL": "postgres://actual-current/app"}
    core_api = MagicMock()
    core_api.read_namespaced_config_map.return_value = mock_cm
    mock_clients = _make_mock_clients(core=core_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await update_configmap_value(cr, connector, AsyncMock())

    assert result["status"] == "skipped"
    assert "expected old_value" in result["reason"]
    core_api.patch_namespaced_config_map.assert_not_called()


# ---------------------------------------------------------------------------
# update_deployment_image
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_deployment_image_success():
    from app.connectors.executors.kubernetes.reference_update import update_deployment_image

    cr = _make_cr({
        "namespace": "default",
        "deployment_name": "my-app",
        "container_name": "app",
        "old_image": "registry.internal/myapp:v1.0",
        "new_image": "registry.internal/myapp:v2.0",
    })
    connector = _make_connector()

    mock_container = MagicMock()
    mock_container.name = "app"
    mock_container.image = "registry.internal/myapp:v1.0"
    mock_deployment = MagicMock()
    mock_deployment.spec.template.spec.containers = [mock_container]
    apps_api = MagicMock()
    apps_api.read_namespaced_deployment.return_value = mock_deployment
    mock_clients = _make_mock_clients(apps=apps_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await update_deployment_image(cr, connector, AsyncMock())

    assert result["status"] == "success"
    assert result["rollback_data"]["old_image"] == "registry.internal/myapp:v1.0"
    assert mock_container.image == "registry.internal/myapp:v2.0"
    apps_api.patch_namespaced_deployment.assert_called_once()


@pytest.mark.asyncio
async def test_update_deployment_image_old_value_mismatch():
    from app.connectors.executors.kubernetes.reference_update import update_deployment_image

    cr = _make_cr({
        "namespace": "default",
        "deployment_name": "my-app",
        "container_name": "app",
        "old_image": "registry.internal/myapp:v1.0",
        "new_image": "registry.internal/myapp:v2.0",
    })
    connector = _make_connector()

    mock_container = MagicMock()
    mock_container.name = "app"
    mock_container.image = "registry.internal/myapp:v3.0"  # already at v3, not v1
    mock_deployment = MagicMock()
    mock_deployment.spec.template.spec.containers = [mock_container]
    apps_api = MagicMock()
    apps_api.read_namespaced_deployment.return_value = mock_deployment
    mock_clients = _make_mock_clients(apps=apps_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await update_deployment_image(cr, connector, AsyncMock())

    assert result["status"] == "skipped"
    assert "expected old_image" in result["reason"]
    apps_api.patch_namespaced_deployment.assert_not_called()


# ---------------------------------------------------------------------------
# update_deployment_env_var
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_deployment_env_var_success():
    from app.connectors.executors.kubernetes.reference_update import update_deployment_env_var

    cr = _make_cr({
        "namespace": "staging",
        "deployment_name": "backend",
        "container_name": "api",
        "env_var_name": "DB_HOST",
        "old_value": "old-db.internal",
        "new_value": "new-db.internal",
    })
    connector = _make_connector()

    mock_env = MagicMock()
    mock_env.name = "DB_HOST"
    mock_env.value = "old-db.internal"
    mock_container = MagicMock()
    mock_container.name = "api"
    mock_container.env = [mock_env]
    mock_deployment = MagicMock()
    mock_deployment.spec.template.spec.containers = [mock_container]
    apps_api = MagicMock()
    apps_api.read_namespaced_deployment.return_value = mock_deployment
    mock_clients = _make_mock_clients(apps=apps_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await update_deployment_env_var(cr, connector, AsyncMock())

    assert result["status"] == "success"
    assert result["rollback_data"]["old_value"] == "old-db.internal"
    assert result["rollback_data"]["env_var_name"] == "DB_HOST"
    assert mock_env.value == "new-db.internal"
    apps_api.patch_namespaced_deployment.assert_called_once()


@pytest.mark.asyncio
async def test_update_deployment_env_var_old_value_mismatch():
    from app.connectors.executors.kubernetes.reference_update import update_deployment_env_var

    cr = _make_cr({
        "namespace": "staging",
        "deployment_name": "backend",
        "container_name": "api",
        "env_var_name": "DB_HOST",
        "old_value": "expected-old-db.internal",
        "new_value": "new-db.internal",
    })
    connector = _make_connector()

    mock_env = MagicMock()
    mock_env.name = "DB_HOST"
    mock_env.value = "actual-current-db.internal"
    mock_container = MagicMock()
    mock_container.name = "api"
    mock_container.env = [mock_env]
    mock_deployment = MagicMock()
    mock_deployment.spec.template.spec.containers = [mock_container]
    apps_api = MagicMock()
    apps_api.read_namespaced_deployment.return_value = mock_deployment
    mock_clients = _make_mock_clients(apps=apps_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await update_deployment_env_var(cr, connector, AsyncMock())

    assert result["status"] == "skipped"
    assert "expected old_value" in result["reason"]
    apps_api.patch_namespaced_deployment.assert_not_called()


# ---------------------------------------------------------------------------
# Rollback tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_configmap_value():
    from app.connectors.executors.kubernetes.reference_update import rollback_configmap_value

    cr = _make_cr({})
    cr.execution_result = {
        "rollback_data": {
            "namespace": "production",
            "configmap_name": "app-config",
            "key": "DATABASE_URL",
            "old_value": "postgres://old-db.internal/app",
        }
    }
    connector = _make_connector()

    mock_cm = MagicMock()
    mock_cm.data = {"DATABASE_URL": "postgres://new-db.internal/app"}
    core_api = MagicMock()
    core_api.read_namespaced_config_map.return_value = mock_cm
    mock_clients = _make_mock_clients(core=core_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await rollback_configmap_value(cr, connector, AsyncMock())

    assert result["status"] == "success"
    core_api.patch_namespaced_config_map.assert_called_once()
    patched_body = core_api.patch_namespaced_config_map.call_args[1]["body"]
    assert patched_body.data["DATABASE_URL"] == "postgres://old-db.internal/app"


@pytest.mark.asyncio
async def test_rollback_deployment_env_var():
    from app.connectors.executors.kubernetes.reference_update import rollback_deployment_env_var

    cr = _make_cr({})
    cr.execution_result = {
        "rollback_data": {
            "namespace": "staging",
            "deployment_name": "backend",
            "container_name": "api",
            "env_var_name": "DB_HOST",
            "old_value": "old-db.internal",
        }
    }
    connector = _make_connector()

    mock_env = MagicMock()
    mock_env.name = "DB_HOST"
    mock_env.value = "new-db.internal"
    mock_container = MagicMock()
    mock_container.name = "api"
    mock_container.env = [mock_env]
    mock_deployment = MagicMock()
    mock_deployment.spec.template.spec.containers = [mock_container]
    apps_api = MagicMock()
    apps_api.read_namespaced_deployment.return_value = mock_deployment
    mock_clients = _make_mock_clients(apps=apps_api)

    with patch("app.connectors.executors.kubernetes.reference_update.get_k8s_client", return_value=mock_clients):
        result = await rollback_deployment_env_var(cr, connector, AsyncMock())

    assert result["status"] == "success"
    assert mock_env.value == "old-db.internal"
    apps_api.patch_namespaced_deployment.assert_called_once()
