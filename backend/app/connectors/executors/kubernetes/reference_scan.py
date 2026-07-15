# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Kubernetes scan executors — unified hit schema for reference_scan orchestrator."""

import logging
from app.connectors.executors.kubernetes._client import get_k8s_client

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

logger = logging.getLogger(__name__)


def _matches(value, search_terms):
    if not value:
        return []
    return [t for t in search_terms if t.lower() in value.lower()]


def _hit(surface, location, matched_term, snippet, namespace, kind, name, node_name=None, pod_ip=None):
    return {
        "surface": surface,
        "location": location,
        "matched_term": matched_term,
        "snippet": snippet,
        "consumer_identity": {
            "stable_id": f"{namespace}/{kind}/{name}",
            "hostname": node_name or pod_ip,
            "surface_metadata": {
                "namespace": namespace,
                "kind": kind,
                "name": name,
            },
        },
    }


async def scan_configmaps(cr, connector, db):
    """Scans ConfigMap data values across all namespaces for matching terms."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or []

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"hits": [], "scanned": 0, "error": "No kubeconfig available"}

    core_api = clients["core"]
    hits = []
    scanned = 0

    if namespaces:
        items = []
        for ns in namespaces:
            items.extend(core_api.list_namespaced_config_map(namespace=ns).items)
    else:
        items = core_api.list_config_map_for_all_namespaces().items

    for cm in items:
        scanned += 1
        ns = cm.metadata.namespace
        name = cm.metadata.name
        location = f"{ns}/ConfigMap/{name}"
        for key, val in (cm.data or {}).items():
            for term in _matches(val, search_terms):
                hits.append(_hit(
                    surface="k8s_configmap",
                    location=location,
                    matched_term=term,
                    snippet=f"{key}={val}",
                    namespace=ns,
                    kind="ConfigMap",
                    name=name,
                ))

    return {"hits": hits, "scanned": scanned}


async def scan_secrets_metadata(cr, connector, db):
    """Scans Secret names and labels ONLY — never secret values."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or []

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"hits": [], "scanned": 0, "error": "No kubeconfig available"}

    core_api = clients["core"]
    hits = []
    scanned = 0

    if namespaces:
        items = []
        for ns in namespaces:
            items.extend(core_api.list_namespaced_secret(namespace=ns).items)
    else:
        items = core_api.list_secret_for_all_namespaces().items

    for secret in items:
        scanned += 1
        ns = secret.metadata.namespace
        name = secret.metadata.name
        labels = secret.metadata.labels or {}
        labels_str = " ".join(f"{k}={v}" for k, v in labels.items())
        location = f"{ns}/Secret/{name}"

        # Scan name
        for term in _matches(name, search_terms):
            hits.append(_hit(
                surface="k8s_secret_metadata",
                location=location,
                matched_term=term,
                snippet=f"name={name}",
                namespace=ns,
                kind="Secret",
                name=name,
            ))

        # Scan labels (keys and values — never secret data values)
        for term in _matches(labels_str, search_terms):
            hits.append(_hit(
                surface="k8s_secret_metadata",
                location=location,
                matched_term=term,
                snippet=f"labels: {labels_str}",
                namespace=ns,
                kind="Secret",
                name=name,
            ))

    return {"hits": hits, "scanned": scanned}


async def scan_deployment_env(cr, connector, db):
    """Scans Deployment and StatefulSet container environment variables across all namespaces."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or []

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"hits": [], "scanned": 0, "error": "No kubeconfig available"}

    apps_api = clients["apps"]
    hits = []
    scanned = 0

    for kind, list_namespaced, list_all in [
        ("Deployment", apps_api.list_namespaced_deployment, apps_api.list_deployment_for_all_namespaces),
        ("StatefulSet", apps_api.list_namespaced_stateful_set, apps_api.list_stateful_set_for_all_namespaces),
    ]:
        if namespaces:
            items = []
            for ns in namespaces:
                items.extend(list_namespaced(namespace=ns).items)
        else:
            items = list_all().items

        for obj in items:
            scanned += 1
            ns = obj.metadata.namespace
            name = obj.metadata.name
            location = f"{ns}/{kind}/{name}"
            containers = (obj.spec.template.spec.containers or []) if obj.spec and obj.spec.template and obj.spec.template.spec else []
            for container in containers:
                for env in (container.env or []):
                    val = env.value or ""
                    for term in _matches(val, search_terms):
                        hits.append(_hit(
                            surface="k8s_deployment_spec",
                            location=location,
                            matched_term=term,
                            snippet=f"{env.name}={val} (container: {container.name})",
                            namespace=ns,
                            kind=kind,
                            name=name,
                        ))

            # Also scan image names
            for container in containers:
                image = container.image or ""
                for term in _matches(image, search_terms):
                    hits.append(_hit(
                        surface="k8s_deployment_spec",
                        location=location,
                        matched_term=term,
                        snippet=f"image={image} (container: {container.name})",
                        namespace=ns,
                        kind=kind,
                        name=name,
                    ))

    return {"hits": hits, "scanned": scanned}


async def scan_ingress_rules(cr, connector, db):
    """Scans Ingress rules including host and backend service references across all namespaces."""
    params = cr.parameters or {}
    search_terms = params.get("search_terms", [])
    namespaces = params.get("namespaces") or []

    clients = get_k8s_client(connector, params)
    if clients is None:
        return {"hits": [], "scanned": 0, "error": "No kubeconfig available"}

    networking_api = clients["networking"]
    hits = []
    scanned = 0

    if namespaces:
        items = []
        for ns in namespaces:
            items.extend(networking_api.list_namespaced_ingress(namespace=ns).items)
    else:
        items = networking_api.list_ingress_for_all_namespaces().items

    for ingress in items:
        scanned += 1
        ns = ingress.metadata.namespace
        name = ingress.metadata.name
        location = f"{ns}/Ingress/{name}"

        for rule in (ingress.spec.rules or []) if ingress.spec else []:
            host = rule.host or ""
            for term in _matches(host, search_terms):
                hits.append(_hit(
                    surface="k8s_ingress",
                    location=location,
                    matched_term=term,
                    snippet=f"host={host}",
                    namespace=ns,
                    kind="Ingress",
                    name=name,
                ))

            for path_obj in (rule.http.paths if rule.http else []):
                svc_name = ""
                if path_obj.backend and path_obj.backend.service:
                    svc_name = path_obj.backend.service.name or ""
                for term in _matches(svc_name, search_terms):
                    hits.append(_hit(
                        surface="k8s_ingress",
                        location=location,
                        matched_term=term,
                        snippet=f"backend.service.name={svc_name} (path: {path_obj.path})",
                        namespace=ns,
                        kind="Ingress",
                        name=name,
                    ))

    return {"hits": hits, "scanned": scanned}
