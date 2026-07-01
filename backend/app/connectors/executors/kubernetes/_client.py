# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import base64
import yaml
from kubernetes import client, config


def get_k8s_clients(creds: dict):
    kubeconfig_raw = creds.get("kubeconfig", "")
    if not kubeconfig_raw:
        raise ValueError("kubeconfig is required in credentials")

    # Support both raw YAML and base64-encoded YAML
    try:
        kubeconfig_yaml = base64.b64decode(kubeconfig_raw).decode("utf-8")
    except Exception:
        kubeconfig_yaml = kubeconfig_raw  # already raw YAML

    kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
    cfg = client.Configuration()
    config.load_kube_config_from_dict(
        config_dict=kubeconfig_dict,
        client_configuration=cfg,
        context=creds.get("context"),
    )
    api_client = client.ApiClient(cfg)
    return {
        "core": client.CoreV1Api(api_client),
        "apps": client.AppsV1Api(api_client),
        "rbac": client.RbacAuthorizationV1Api(api_client),
        "networking": client.NetworkingV1Api(api_client),
        "api_client": api_client,
    }


def get_k8s_client(connector, parameters: dict | None = None):
    """Return kubernetes API clients dict.

    Priority:
    1. 'kubeconfig' in parameters (raw YAML string, used by smoke tests)
    2. connector.credentials['kubeconfig'] (base64 or raw YAML)

    Returns None if no kubeconfig is available so callers can return 'skipped'.
    """
    kubeconfig = None

    if parameters:
        kubeconfig = parameters.get("kubeconfig", "")

    if not kubeconfig:
        creds = getattr(connector, "credentials", {}) or {}
        kubeconfig = creds.get("kubeconfig", "")

    if not kubeconfig:
        return None

    return get_k8s_clients({"kubeconfig": kubeconfig})
