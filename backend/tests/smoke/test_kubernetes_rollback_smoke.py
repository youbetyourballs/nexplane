# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import os
import time
import pytest
import sys
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@nexplane.local")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin")

PHASE = "K8S_ROLLBACK"
TIMEOUT = 120
SMOKE_DEPLOYMENT = "nexplane-smoke-rollback"
SMOKE_NAMESPACE = "default"
INITIAL_REPLICAS = 2


def _run_cr(client, label, action_id, params, timeout=TIMEOUT):
    base = client.base
    resp = client.client.post(f"{base}/change-requests", json={
        "title": label,
        "change_type": "catalog_action",
        "desired_outcome": {"connector_type": "kubernetes", "action_id": action_id, "params": params},
    })
    if resp.status_code not in (200, 201):
        raise AssertionError(f"[{label}] CR create failed {resp.status_code}: {resp.text}")
    cr_id = resp.json()["id"]

    for path in ["plan", "submit-for-approval"]:
        r = client.client.post(f"{base}/change-requests/{cr_id}/{path}")
        if r.status_code not in (200, 201, 202, 204):
            raise AssertionError(f"[{label}] /{path} failed {r.status_code}: {r.text}")

    r = client.client.post(f"{base}/change-requests/{cr_id}/approve",
        json={"decision": "approved", "comment": "smoke"})
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /approve failed {r.status_code}: {r.text}")

    r = client.client.post(f"{base}/change-requests/{cr_id}/execute")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /execute failed {r.status_code}: {r.text}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "completed":
            log(label)
            return cr
        if status in ("failed", "rejected", "cancelled"):
            raise AssertionError(
                f"[{label}] CR {cr_id} status={status!r}: {str(cr.get('execution_runs', ''))[:400]}"
            )
        time.sleep(10)
    raise TimeoutError(f"[{label}] CR {cr_id} timeout after {timeout}s")


def _rollback_cr(client, cr_id, label, timeout=TIMEOUT):
    base = client.base
    r = client.client.post(f"{base}/change-requests/{cr_id}/rollback")
    if r.status_code not in (200, 201, 202, 204):
        raise AssertionError(f"[{label}] /rollback failed {r.status_code}: {r.text}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        cr = client.client.get(f"{base}/change-requests/{cr_id}").json()
        status = cr.get("status", "")
        if status == "rolled_back":
            log(f"rolled back: {label}")
            return cr
        if status in ("rollback_failed", "failed"):
            raise AssertionError(f"[{label}] rollback status={status!r}")
        time.sleep(10)
    raise TimeoutError(f"[{label}] rollback timeout after {timeout}s")


def _step_result(cr, rollback=False):
    for run in cr.get("execution_runs", []):
        is_rb = "rollback" in run.get("workflow_id", "")
        if is_rb != rollback:
            continue
        result = run.get("result") or {}
        if rollback:
            steps = result.get("rollback_steps", [])
            if steps:
                return steps[0].get("result") or {}
        else:
            steps = result.get("execution", {}).get("steps", [])
            if steps:
                return steps[0].get("result") or {}
    return cr.get("execution_result") or {}


def _get_k8s_apps_client(creds):
    from kubernetes import client as k8s_client, config as k8s_config
    kubeconfig_raw = creds.get("kubeconfig")
    if kubeconfig_raw:
        kubeconfig_dict = yaml.safe_load(kubeconfig_raw) if isinstance(kubeconfig_raw, str) else kubeconfig_raw
        k8s_config.load_kube_config_from_dict(kubeconfig_dict)
    else:
        # Try server + token approach
        server = creds.get("server")
        token = creds.get("token")
        if not server or not token:
            pytest.skip("No kubeconfig or server/token in Kubernetes creds")
        configuration = k8s_client.Configuration()
        configuration.host = server
        configuration.api_key = {"authorization": f"Bearer {token}"}
        configuration.verify_ssl = False
        k8s_client.Configuration.set_default(configuration)
    return k8s_client.AppsV1Api()


def _get_deployment_replicas(apps_v1, name, namespace):
    try:
        dep = apps_v1.read_namespaced_deployment(name, namespace)
        return dep.spec.replicas
    except Exception:
        return None


def _ensure_test_deployment(apps_v1, name, namespace, replicas):
    from kubernetes import client as k8s_client
    existing = _get_deployment_replicas(apps_v1, name, namespace)
    if existing is not None:
        log(f"{PHASE}: existing deployment {name} found with {existing} replicas")
        # If replicas don't match, patch to desired
        if existing != replicas:
            apps_v1.patch_namespaced_deployment(
                name,
                namespace,
                {"spec": {"replicas": replicas}},
            )
            time.sleep(5)
        return

    # Create the deployment
    deployment = k8s_client.V1Deployment(
        metadata=k8s_client.V1ObjectMeta(name=name, namespace=namespace),
        spec=k8s_client.V1DeploymentSpec(
            replicas=replicas,
            selector=k8s_client.V1LabelSelector(
                match_labels={"app": name},
            ),
            template=k8s_client.V1PodTemplateSpec(
                metadata=k8s_client.V1ObjectMeta(labels={"app": name}),
                spec=k8s_client.V1PodSpec(
                    containers=[
                        k8s_client.V1Container(
                            name="nginx",
                            image="nginx:latest",
                        )
                    ]
                ),
            ),
        ),
    )
    apps_v1.create_namespaced_deployment(namespace, deployment)
    log(f"{PHASE}: created test deployment {name} with {replicas} replicas")
    time.sleep(5)


def _delete_test_deployment(apps_v1, name, namespace):
    try:
        apps_v1.delete_namespaced_deployment(name, namespace)
        log(f"{PHASE}: cleanup — deleted test deployment {name}")
    except Exception as e:
        log(f"{PHASE}: cleanup skipped ({e})")


class TestKubernetesRollbackSmoke:
    """
    End-to-end rollback smoke for the Kubernetes scale_deployment executor.
    Creates/finds a test deployment with 2 replicas, scales to 0 via CR,
    rolls back, verifies replicas restored to 2.
    """

    def setup_method(self):
        self.client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
        self.creds = get_connector_creds_from_db("kubernetes")
        if self.creds is None:
            pytest.skip("No Kubernetes credentials in platform DB")
        self.apps_v1 = _get_k8s_apps_client(self.creds)

    def test_scale_deployment_rollback(self):
        try:
            # Step 1: Ensure test deployment exists with INITIAL_REPLICAS
            _ensure_test_deployment(
                self.apps_v1,
                SMOKE_DEPLOYMENT,
                SMOKE_NAMESPACE,
                INITIAL_REPLICAS,
            )

            current_replicas = _get_deployment_replicas(self.apps_v1, SMOKE_DEPLOYMENT, SMOKE_NAMESPACE)
            assert current_replicas == INITIAL_REPLICAS, (
                f"Expected {INITIAL_REPLICAS} replicas before test, got {current_replicas}"
            )
            log(f"{PHASE}: deployment {SMOKE_DEPLOYMENT} confirmed at {INITIAL_REPLICAS} replicas")

            # Step 2: Execute scale_deployment CR → scale to 0
            cr = _run_cr(
                self.client,
                f"[smoke] scale deployment {SMOKE_DEPLOYMENT} to 0",
                "scale_deployment",
                {
                    "deployment_name": SMOKE_DEPLOYMENT,
                    "namespace": SMOKE_NAMESPACE,
                    "replicas": 0,
                },
            )
            cr_id = cr["id"]

            scale_result = _step_result(cr, rollback=False)
            assert scale_result.get("scaled") is True, (
                f"Expected scaled=True, got: {scale_result}"
            )
            assert scale_result.get("new_replicas") == 0, (
                f"Expected new_replicas=0, got: {scale_result}"
            )
            log(f"{PHASE}: scale_deployment to 0 executed (cr={cr_id})")

            # Step 3: Verify via Kubernetes API
            replicas = _get_deployment_replicas(self.apps_v1, SMOKE_DEPLOYMENT, SMOKE_NAMESPACE)
            assert replicas == 0, f"Expected spec.replicas=0 after scale CR, got {replicas}"
            log(f"{PHASE}: k8s API confirmed spec.replicas=0")

            # Step 4: Rollback
            cr = _rollback_cr(self.client, cr_id, f"rollback scale deployment {SMOKE_DEPLOYMENT}")

            rollback_result = _step_result(cr, rollback=True)
            assert rollback_result.get("rolled_back") is True, (
                f"Expected rolled_back=True, got: {rollback_result}"
            )
            log(f"{PHASE}: rollback completed (cr={cr_id})")

            # Step 5: Verify replicas restored
            replicas = _get_deployment_replicas(self.apps_v1, SMOKE_DEPLOYMENT, SMOKE_NAMESPACE)
            assert replicas == INITIAL_REPLICAS, (
                f"Expected spec.replicas={INITIAL_REPLICAS} after rollback, got {replicas}"
            )
            log(f"{PHASE}: k8s API confirmed spec.replicas={INITIAL_REPLICAS} after rollback")

        finally:
            _delete_test_deployment(self.apps_v1, SMOKE_DEPLOYMENT, SMOKE_NAMESPACE)
