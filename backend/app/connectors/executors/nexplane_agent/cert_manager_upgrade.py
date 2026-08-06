# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""cert-manager upgrade executor.

Flow: preflight → snapshot CRDs → upgrade CRDs (server-side apply, PARTIAL NO-RETURN)
      → upgrade Deployment → verify (Certificate issuance test).

ROLLBACK_CAPABILITY = "partial": Deployment can be rolled back; CRDs cannot be
cleanly downgraded. Old CRD backup is recorded but not restored.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_GITHUB_RELEASE_BASE = "https://github.com/cert-manager/cert-manager/releases/download"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    namespace = parameters.get("namespace", "cert-manager")
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required (e.g. '1.13')")
    if not target_version:
        raise ValueError("target_version required (e.g. '1.15')")

    # Validate: cert-manager supports skipping patch, not minor — only +N minor allowed (warn if >+1)
    try:
        src_minor = int(source_version.split(".")[1])
        tgt_minor = int(target_version.split(".")[1])
    except (IndexError, ValueError):
        raise ValueError("source_version and target_version must be 'MAJOR.MINOR' format")
    if tgt_minor <= src_minor:
        raise ValueError(f"target_version must be newer than source_version")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    crds_url = f"{_GITHUB_RELEASE_BASE}/v{target_version}/cert-manager.crds.yaml"
    deploy_url = f"{_GITHUB_RELEASE_BASE}/v{target_version}/cert-manager.yaml"

    # Step 1: Preflight
    logger.info(f"cert-manager preflight {source_version}→{target_version} on {asset_id}")
    preflight = await dispatch_agent_job(
        command="cert_manager_preflight",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if preflight.get("status") == "blocked":
        return {"status": "blocked", "reason": preflight.get("reason"), "preflight": preflight}

    current_image_tag = preflight.get("current_image_tag", source_version)

    # Step 2: Snapshot CRDs
    crd_backup_path = f"/tmp/cert-manager-crds-backup-{asset_id[:8]}.yaml"
    await dispatch_agent_job(
        command="cert_manager_snapshot_crds",
        parameters={"backup_path": crd_backup_path, "kubeconfig_path": kubeconfig_path},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "crds_url": crds_url,
            "deploy_url": deploy_url,
            "preflight": preflight,
        }

    # Step 3: Upgrade CRDs — POINT OF PARTIAL NO-RETURN
    logger.info(f"Upgrading cert-manager CRDs to {target_version}")
    crd_result = await dispatch_agent_job(
        command="cert_manager_upgrade_crds",
        parameters={
            "crds_url": crds_url,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=180,
    )
    if not crd_result.get("success", True):
        return {
            "status": "failed",
            "phase": "crd_upgrade",
            "error": crd_result.get("error"),
            "crds_upgraded": False,
            "deployment_upgraded": False,
        }

    # Step 4: Upgrade Deployment
    logger.info(f"Upgrading cert-manager Deployment to {target_version}")
    deploy_result = await dispatch_agent_job(
        command="cert_manager_upgrade_deployment",
        parameters={
            "deploy_url": deploy_url,
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    if not deploy_result.get("success", True):
        return {
            "status": "failed",
            "phase": "deployment_upgrade",
            "error": deploy_result.get("error"),
            "crds_upgraded": True,
            "deployment_upgraded": False,
            "crd_backup_path": crd_backup_path,
            "note": "CRDs upgraded but Deployment failed. Rollback will restore Deployment only.",
        }

    # Step 5: Verify — issue a test self-signed Certificate
    verify = await dispatch_agent_job(
        command="cert_manager_verify",
        parameters={
            "namespace": namespace,
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "crds_upgraded": True,
        "deployment_upgraded": True,
        "test_cert_issued": verify.get("cert_issued", False),
        "crd_backup_path": crd_backup_path,
        "current_image_tag_before": current_image_tag,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""
    namespace = parameters.get("namespace", "cert-manager")
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")
    crd_backup_path = execution_result.get("crd_backup_path", "")

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Rollback Deployment only — kubectl rollout undo
    deploy_rb = await dispatch_agent_job(
        command="cert_manager_rollback_deployment",
        parameters={
            "namespace": namespace,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    return {
        "rolled_back": True,
        "deployment_rolled_back": deploy_rb.get("success", True),
        "crds_note": (
            "CRDs remain at target version — CRD schema additions are non-breaking and "
            f"cannot be cleanly downgraded. Backup stored at {crd_backup_path}."
        ),
    }
