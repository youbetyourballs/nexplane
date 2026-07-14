# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kubernetes reference update executors — reconstitution rollback pattern."""

import logging
from app.connectors.executors.kubernetes._client import get_k8s_client

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# update_configmap_value
# ---------------------------------------------------------------------------

async def update_configmap_value(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    cm_name = params["configmap_name"]
    key = params["key"]
    old_val = params["old_value"]
    new_val = params["new_value"]

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    core_api = clients["core"]
    cm = core_api.read_namespaced_config_map(name=cm_name, namespace=ns)
    data = dict(cm.data or {})

    if data.get(key) != old_val:
        return {
            "status": "skipped",
            "reason": f"current value '{data.get(key)}' != expected old_value '{old_val}'",
        }

    data[key] = new_val
    cm.data = data
    core_api.patch_namespaced_config_map(name=cm_name, namespace=ns, body=cm)

    return {
        "status": "updated",
        "rollback_data": {
            "namespace": ns,
            "configmap_name": cm_name,
            "key": key,
            "old_value": old_val,
        },
    }


async def rollback_configmap_value(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    clients = get_k8s_client(connector)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    core_api = clients["core"]
    cm = core_api.read_namespaced_config_map(name=rb["configmap_name"], namespace=rb["namespace"])
    data = dict(cm.data or {})
    data[rb["key"]] = rb["old_value"]
    cm.data = data
    core_api.patch_namespaced_config_map(name=rb["configmap_name"], namespace=rb["namespace"], body=cm)
    return {"status": "success"}


# ---------------------------------------------------------------------------
# update_deployment_image
# ---------------------------------------------------------------------------

async def update_deployment_image(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    deployment_name = params["deployment_name"]
    container_name = params["container_name"]
    old_image = params["old_image"]
    new_image = params["new_image"]

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    apps_api = clients["apps"]
    obj = apps_api.read_namespaced_deployment(name=deployment_name, namespace=ns)

    containers = (obj.spec.template.spec.containers or []) if obj.spec and obj.spec.template and obj.spec.template.spec else []
    updated = False
    for container in containers:
        if container.name == container_name:
            if container.image != old_image:
                return {
                    "status": "skipped",
                    "reason": f"current image '{container.image}' != expected old_image '{old_image}'",
                }
            container.image = new_image
            updated = True
            break

    if not updated:
        return {
            "status": "skipped",
            "reason": f"container '{container_name}' not found in deployment '{deployment_name}'",
        }

    apps_api.patch_namespaced_deployment(name=deployment_name, namespace=ns, body=obj)

    return {
        "status": "success",
        "rollback_data": {
            "namespace": ns,
            "deployment_name": deployment_name,
            "container_name": container_name,
            "old_image": old_image,
        },
    }


async def rollback_deployment_image(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    clients = get_k8s_client(connector)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    apps_api = clients["apps"]
    obj = apps_api.read_namespaced_deployment(name=rb["deployment_name"], namespace=rb["namespace"])
    containers = (obj.spec.template.spec.containers or []) if obj.spec and obj.spec.template and obj.spec.template.spec else []
    for container in containers:
        if container.name == rb["container_name"]:
            container.image = rb["old_image"]
            break
    apps_api.patch_namespaced_deployment(name=rb["deployment_name"], namespace=rb["namespace"], body=obj)
    return {"status": "success"}


# ---------------------------------------------------------------------------
# update_deployment_env_var
# ---------------------------------------------------------------------------

async def update_deployment_env_var(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    deployment_name = params["deployment_name"]
    container_name = params["container_name"]
    env_var_name = params["env_var_name"]
    old_val = params["old_value"]
    new_val = params["new_value"]

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    apps_api = clients["apps"]
    obj = apps_api.read_namespaced_deployment(name=deployment_name, namespace=ns)

    containers = (obj.spec.template.spec.containers or []) if obj.spec and obj.spec.template and obj.spec.template.spec else []
    updated = False
    for container in containers:
        if container.name == container_name:
            for env in (container.env or []):
                if env.name == env_var_name:
                    if env.value != old_val:
                        return {
                            "status": "skipped",
                            "reason": f"current value '{env.value}' != expected old_value '{old_val}'",
                        }
                    env.value = new_val
                    updated = True
                    break
            break

    if not updated:
        return {
            "status": "skipped",
            "reason": f"env var '{env_var_name}' not found in container '{container_name}'",
        }

    apps_api.patch_namespaced_deployment(name=deployment_name, namespace=ns, body=obj)

    return {
        "status": "updated",
        "rollback_data": {
            "namespace": ns,
            "deployment_name": deployment_name,
            "container_name": container_name,
            "env_var_name": env_var_name,
            "old_value": old_val,
        },
    }


async def update_ingress_host(cr, connector, db) -> dict:
    params = cr.parameters or {}
    ns = params["namespace"]
    ingress_name = params["ingress_name"]
    old_host = params["old_host"]
    new_host = params["new_host"]

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    networking_api = clients["networking"]
    ingress = networking_api.read_namespaced_ingress(name=ingress_name, namespace=ns)
    updated = False
    for rule in (ingress.spec.rules or []):
        if rule.host == old_host:
            rule.host = new_host
            updated = True

    if not updated:
        return {"status": "skipped", "reason": f"host {old_host} not found in ingress rules"}

    networking_api.patch_namespaced_ingress(name=ingress_name, namespace=ns, body=ingress)
    return {
        "status": "updated",
        "rollback_data": {
            "namespace": ns,
            "ingress_name": ingress_name,
            "old_host": old_host,
            "new_host": new_host,
        },
    }


async def rollback_ingress_host(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    clients = get_k8s_client(connector)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    networking_api = clients["networking"]
    ingress = networking_api.read_namespaced_ingress(name=rb["ingress_name"], namespace=rb["namespace"])
    for rule in (ingress.spec.rules or []):
        if rule.host == rb["new_host"]:
            rule.host = rb["old_host"]
    networking_api.patch_namespaced_ingress(name=rb["ingress_name"], namespace=rb["namespace"], body=ingress)
    return {"status": "rolled_back"}


async def rollback_deployment_env_var(cr, connector, db) -> dict:
    rb = (cr.execution_result or {}).get("rollback_data", {})
    clients = get_k8s_client(connector)
    if clients is None:
        return {"status": "error", "reason": "No kubeconfig available"}

    apps_api = clients["apps"]
    obj = apps_api.read_namespaced_deployment(name=rb["deployment_name"], namespace=rb["namespace"])
    containers = (obj.spec.template.spec.containers or []) if obj.spec and obj.spec.template and obj.spec.template.spec else []
    for container in containers:
        if container.name == rb["container_name"]:
            for env in (container.env or []):
                if env.name == rb["env_var_name"]:
                    env.value = rb["old_value"]
                    break
            break
    apps_api.patch_namespaced_deployment(name=rb["deployment_name"], namespace=rb["namespace"], body=obj)
    return {"status": "success"}
