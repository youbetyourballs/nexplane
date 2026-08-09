# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""cert-manager upgrade executor.

Flow: preflight -> upgrade CRDs (PARTIAL NO-RETURN) -> upgrade Deployment (helm)
      -> wait for rollout -> verify pods.

ROLLBACK_CAPABILITY = "full": helm rollback restores previous Deployment revision.
CRDs cannot be cleanly downgraded; they remain at target version after rollback.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_GITHUB_RELEASE_BASE = "https://github.com/cert-manager/cert-manager/releases/download"


def _get_dispatch():
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return dispatch_agent_job


async def _run(command: str, asset_id: str, timeout: int = 120) -> dict:
    fn = _get_dispatch()
    return await fn(
        command="run_command",
        parameters={"command": command, "timeout": timeout},
        asset_ids=[asset_id],
        timeout_seconds=timeout + 30,
    )


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    namespace = p.get("namespace", "cert-manager")
    kubeconfig_path = p.get("kubeconfig_path", "/etc/rancher/k3s/k3s.yaml")
    source_version = p.get("source_version", "")
    target_version = p.get("target_version", "")
    dry_run = bool(p.get("dry_run", False))
    return {
        "source_version": source_version,
        "target_version": target_version,
        "namespace": namespace,
        "kubeconfig_path": kubeconfig_path,
        "dry_run": dry_run,
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)
    source_version = p["source_version"]
    target_version = p["target_version"]
    namespace = p["namespace"]
    kubeconfig_path = p["kubeconfig_path"]
    dry_run = p["dry_run"]

    if not source_version:
        raise ValueError("source_version required (e.g. '1.13.0')")
    if not target_version:
        raise ValueError("target_version required (e.g. '1.15.0')")

    try:
        src_minor = int(source_version.split(".")[1])
        tgt_minor = int(target_version.split(".")[1])
    except (IndexError, ValueError):
        raise ValueError("source_version and target_version must be 'MAJOR.MINOR.PATCH' format")
    if tgt_minor <= src_minor:
        raise ValueError("target_version must be newer than source_version")

    kubectl = f"/usr/local/bin/k3s kubectl --kubeconfig={kubeconfig_path}"
    helm = f"/usr/local/bin/helm --kubeconfig={kubeconfig_path}"
    crds_url = f"{_GITHUB_RELEASE_BASE}/v{target_version}/cert-manager.crds.yaml"

    # --- Step 0: Wait for k3s kubeconfig (k3s service may still be starting) ---
    r = await _run(
        f"for i in $(seq 1 120); do "
        f"[ -f {kubeconfig_path} ] && echo KUBECONFIG_OK && break; "
        f"if [ $i -eq 12 ]; then systemctl reset-failed k3s 2>/dev/null; systemctl start k3s 2>/dev/null || true; fi; "
        f"if [ $i -eq 60 ]; then systemctl reset-failed k3s 2>/dev/null; systemctl restart k3s 2>/dev/null || true; fi; "
        f"sleep 5; done; "
        f"[ -f {kubeconfig_path} ] || echo KUBECONFIG_MISSING",
        asset_id, timeout=630,
    )
    if "KUBECONFIG_MISSING" in r.get("output", "") or "KUBECONFIG_OK" not in r.get("output", ""):
        return {
            "status": "failed",
            "phase": "preflight",
            "error": f"k3s kubeconfig not found at {kubeconfig_path} after 600s",
            "crds_upgraded": False,
            "deployment_upgraded": False,
            "asset_id": asset_id,
        }

    # --- Step 1: Preflight ---
    logger.info("cert-manager preflight %s->%s on %s", source_version, target_version, asset_id)
    r = await _run(
        f"{helm} list -n {namespace} 2>&1",
        asset_id,
        timeout=60,
    )
    preflight_output = r.get("output", "")
    logger.info("cert-manager preflight output: %s", preflight_output[:300])

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "namespace": namespace,
            "crds_url": crds_url,
            "preflight_output": preflight_output,
        }

    # --- Step 2: Ensure helm repo present and updated ---
    logger.info("Adding/updating jetstack helm repo")
    r = await _run(
        f"{helm} repo add jetstack https://charts.jetstack.io 2>/dev/null; "
        f"{helm} repo update 2>&1",
        asset_id,
        timeout=120,
    )
    logger.info("helm repo update: %s", r.get("output", "")[:200])

    # --- Step 3: Upgrade CRDs --- POINT OF PARTIAL NO-RETURN ---
    logger.info("Upgrading cert-manager CRDs to %s", target_version)
    r = await _run(
        f"{kubectl} apply --validate=false -f {crds_url} 2>&1; echo CRD_EXIT=$?",
        asset_id,
        timeout=180,
    )
    crd_output = r.get("output", "")
    crd_ok = "CRD_EXIT=0" in crd_output
    logger.info("CRD upgrade result: %s", crd_output[:300])

    if not crd_ok:
        return {
            "status": "failed",
            "phase": "crd_upgrade",
            "error": crd_output,
            "crds_upgraded": False,
            "deployment_upgraded": False,
        }

    # --- Step 4: Upgrade Deployment via helm ---
    logger.info("helm upgrade cert-manager to %s", target_version)
    r = await _run(
        f"{helm} upgrade cert-manager jetstack/cert-manager "
        f"--namespace {namespace} --version {target_version} --reset-values --set installCRDs=false 2>&1; echo HELM_EXIT=$?",
        asset_id,
        timeout=300,
    )
    helm_output = r.get("output", "")
    helm_ok = "HELM_EXIT=0" in helm_output
    logger.info("helm upgrade result: %s", helm_output[:300])

    if not helm_ok:
        return {
            "status": "failed",
            "phase": "deployment_upgrade",
            "error": helm_output,
            "crds_upgraded": True,
            "deployment_upgraded": False,
            "note": "CRDs upgraded but helm upgrade failed. Rollback will restore Deployment only.",
        }

    # --- Step 5: Wait for rollout ---
    logger.info("Waiting for cert-manager rollout")
    r = await _run(
        f"{kubectl} rollout status deployment/cert-manager "
        f"-n {namespace} --timeout=300s 2>&1; echo ROLLOUT_EXIT=$?",
        asset_id,
        timeout=360,
    )
    rollout_output = r.get("output", "")
    rollout_ok = "ROLLOUT_EXIT=0" in rollout_output
    logger.info("Rollout status: %s", rollout_output[:200])

    # --- Step 6: Verify ---
    r_pods = await _run(
        f"{kubectl} get pods -n {namespace} 2>&1",
        asset_id,
        timeout=60,
    )
    r_ver = await _run(
        f"{kubectl} version --client 2>&1",
        asset_id,
        timeout=30,
    )

    pods_output = r_pods.get("output", "")
    version_output = r_ver.get("output", "")

    return {
        "status": "completed" if (helm_ok and rollout_ok) else "verify_warning",
        "source_version": source_version,
        "target_version": target_version,
        "crds_upgraded": True,
        "deployment_upgraded": True,
        "rollout_ok": rollout_ok,
        "pods_output": pods_output[:500],
        "version_output": version_output[:200],
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
        "asset_id": asset_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_id = execution_result.get("asset_id") or str(
        (
            execution_result.get("_target_asset_ids")
            or parameters.get("asset_ids")
            or [None]
        )[0]
    )
    p = _resolve_params(parameters)
    namespace = p["namespace"]
    kubeconfig_path = p["kubeconfig_path"]
    kubectl = f"/usr/local/bin/k3s kubectl --kubeconfig={kubeconfig_path}"
    helm = f"/usr/local/bin/helm --kubeconfig={kubeconfig_path}"

    # Wait for kubeconfig (k3s may still be starting if execute failed early)
    r_kc = await _run(
        f"for i in $(seq 1 36); do "
        f"[ -f {kubeconfig_path} ] && echo KUBECONFIG_OK && break; "
        f"if [ $i -eq 6 ]; then systemctl reset-failed k3s 2>/dev/null; systemctl start k3s 2>/dev/null || true; fi; "
        f"sleep 5; done; "
        f"[ -f {kubeconfig_path} ] || echo KUBECONFIG_MISSING",
        asset_id, timeout=200,
    )
    if "KUBECONFIG_OK" not in r_kc.get("output", ""):
        return {
            "rolled_back": False,
            "reason": f"k3s kubeconfig not found at {kubeconfig_path} — rollback skipped",
            "crds_note": "CRDs remain at target version",
        }

    # helm rollback 0 returns to the previous release revision
    logger.info("Rolling back cert-manager Deployment via helm on %s", asset_id)
    r = await _run(
        f"{helm} rollback cert-manager 0 -n {namespace} 2>&1; echo ROLLBACK_EXIT=$?",
        asset_id,
        timeout=180,
    )
    rb_output = r.get("output", "")
    rb_ok = "ROLLBACK_EXIT=0" in rb_output
    logger.info("helm rollback result: %s", rb_output[:300])

    # Wait for rollout after rollback
    r2 = await _run(
        f"{kubectl} rollout status deployment/cert-manager "
        f"-n {namespace} --timeout=120s 2>&1",
        asset_id,
        timeout=150,
    )
    rollout_output = r2.get("output", "")

    return {
        "rolled_back": rb_ok,
        "deployment_rolled_back": rb_ok,
        "rollback_output": rb_output[:500],
        "rollout_output": rollout_output[:300],
        "crds_note": (
            "CRDs remain at target version -- CRD schema additions are non-breaking and "
            "cannot be cleanly downgraded."
        ),
    }
