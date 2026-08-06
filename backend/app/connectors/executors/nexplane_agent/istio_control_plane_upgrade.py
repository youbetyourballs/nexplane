# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Istio control plane upgrade executor.

Canary strategy (default, recommended):
  Install new revision alongside old → migrate namespaces one by one
  (relabel + rollout restart) → verify all proxies on new version →
  remove old revision.

Rollback is FULL until step 6 (old revision removal). After old revision
is removed the upgrade is irreversible. This is surfaced in execution_result.

In-place strategy: istioctl upgrade -y — simpler, no canary safety net.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"  # full until old revision removed; partial after


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    source_version = parameters.get("source_version", "")
    target_version = parameters.get("target_version", "")
    upgrade_strategy = parameters.get("upgrade_strategy", "canary")
    namespaces = parameters.get("namespaces")  # None = all labeled
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")
    dry_run = bool(parameters.get("dry_run", False))

    if not source_version:
        raise ValueError("source_version required (e.g. '1.20')")
    if not target_version:
        raise ValueError("target_version required (e.g. '1.21')")

    # Validate +1 minor only
    try:
        src_minor = int(source_version.split(".")[1])
        tgt_minor = int(target_version.split(".")[1])
    except (IndexError, ValueError):
        raise ValueError(f"source_version and target_version must be 'MAJOR.MINOR' format")
    if tgt_minor != src_minor + 1:
        raise ValueError(
            f"Istio only supports +1 minor upgrades; got {source_version}→{target_version}"
        )

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Revision labels: Istio uses e.g. "1-20" for 1.20
    source_rev = source_version.replace(".", "-")
    target_rev = target_version.replace(".", "-")

    # Step 1: Preflight
    logger.info(f"Istio upgrade preflight: {source_version}→{target_version} on {asset_id}")
    preflight = await dispatch_agent_job(
        command="istio_preflight",
        parameters={
            "source_version": source_version,
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    if preflight.get("status") == "blocked":
        return {
            "status": "blocked",
            "reason": preflight.get("reason", "Istio preflight failed"),
            "preflight": preflight,
        }

    # Step 2: Snapshot — record current namespace revision labels
    snapshot = await dispatch_agent_job(
        command="istio_snapshot_namespace_labels",
        parameters={"kubeconfig_path": kubeconfig_path, "namespaces": namespaces},
        asset_ids=[asset_id],
        timeout_seconds=120,
    )
    labeled_namespaces = snapshot.get("labeled_namespaces", namespaces or [])

    if dry_run:
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "upgrade_strategy": upgrade_strategy,
            "labeled_namespaces": labeled_namespaces,
            "preflight": preflight,
        }

    if upgrade_strategy == "in_place":
        return await _in_place_upgrade(
            asset_id, source_version, target_version, kubeconfig_path,
            labeled_namespaces, dispatch_agent_job
        )

    # Canary strategy
    # Step 3: Install new revision
    logger.info(f"Installing Istio revision {target_rev} on {asset_id}")
    install_result = await dispatch_agent_job(
        command="istio_install_revision",
        parameters={
            "revision": target_rev,
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=600,
    )
    if not install_result.get("success", True):
        return {
            "status": "failed",
            "phase": "install_revision",
            "error": install_result.get("error", "Failed to install new Istio revision"),
            "source_version": source_version,
            "target_version": target_version,
        }

    # Step 4: Migrate namespaces one by one
    namespaces_migrated = []
    namespace_errors = []
    for ns in labeled_namespaces:
        logger.info(f"Migrating namespace {ns} to Istio {target_rev}")
        try:
            ns_result = await dispatch_agent_job(
                command="istio_migrate_namespace",
                parameters={
                    "namespace": ns,
                    "source_rev": source_rev,
                    "target_rev": target_rev,
                    "kubeconfig_path": kubeconfig_path,
                },
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            if ns_result.get("success", True):
                namespaces_migrated.append(ns)
            else:
                namespace_errors.append({"namespace": ns, "error": ns_result.get("error")})
        except Exception as exc:
            logger.error(f"Namespace {ns} migration failed: {exc}")
            namespace_errors.append({"namespace": ns, "error": str(exc)})

    if namespace_errors:
        return {
            "status": "partial_failure",
            "phase": "namespace_migration",
            "namespaces_migrated": namespaces_migrated,
            "namespace_errors": namespace_errors,
            "source_version": source_version,
            "target_version": target_version,
            "source_rev": source_rev,
            "target_rev": target_rev,
            "old_revision_removed": False,
            "rollback_available": True,
        }

    # Step 5: Verify all proxies on target version
    verify = await dispatch_agent_job(
        command="istio_verify_proxy_status",
        parameters={"target_version": target_version, "kubeconfig_path": kubeconfig_path},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )

    # Step 6: Remove old revision (POINT OF NO RETURN)
    logger.info(f"Removing old Istio revision {source_rev}")
    remove_result = await dispatch_agent_job(
        command="istio_remove_revision",
        parameters={"revision": source_rev, "kubeconfig_path": kubeconfig_path},
        asset_ids=[asset_id],
        timeout_seconds=300,
    )
    old_revision_removed = remove_result.get("success", False)

    return {
        "status": "completed",
        "source_version": source_version,
        "target_version": target_version,
        "upgrade_strategy": "canary",
        "source_rev": source_rev,
        "target_rev": target_rev,
        "namespaces_migrated": namespaces_migrated,
        "namespace_labels_snapshot": labeled_namespaces,
        "proxy_status_verified": verify.get("all_on_target", False),
        "old_revision_removed": old_revision_removed,
        "rollback_available": not old_revision_removed,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def _in_place_upgrade(
    asset_id, source_version, target_version, kubeconfig_path,
    labeled_namespaces, dispatch_agent_job
):
    logger.info(f"Istio in-place upgrade {source_version}→{target_version}")
    result = await dispatch_agent_job(
        command="istio_in_place_upgrade",
        parameters={
            "target_version": target_version,
            "kubeconfig_path": kubeconfig_path,
        },
        asset_ids=[asset_id],
        timeout_seconds=900,
    )
    return {
        "status": "completed" if result.get("success", True) else "failed",
        "source_version": source_version,
        "target_version": target_version,
        "upgrade_strategy": "in_place",
        "namespaces_migrated": labeled_namespaces,
        "old_revision_removed": True,
        "rollback_available": False,
        "agent_result": result,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = (
        execution_result.get("_target_asset_ids")
        or parameters.get("asset_ids")
        or []
    )
    asset_id = str(asset_ids[0]) if asset_ids else ""

    if not execution_result.get("rollback_available", True):
        return {
            "rolled_back": False,
            "reason": "Old Istio revision already removed — canary rollback window has closed. "
                      "Re-install the old revision manually.",
        }

    if execution_result.get("upgrade_strategy") == "in_place":
        return {
            "rolled_back": False,
            "reason": "In-place upgrade is irreversible — manual reinstall required.",
        }

    source_rev = execution_result.get("source_rev", "")
    target_rev = execution_result.get("target_rev", "")
    namespace_labels_snapshot = execution_result.get("namespace_labels_snapshot", [])
    kubeconfig_path = parameters.get("kubeconfig_path", "~/.kube/config")

    if not source_rev:
        return {"rolled_back": False, "reason": "source_rev missing from execution_result"}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    # Re-label namespaces back to source revision (FILO: reverse migration order)
    rolled_back_namespaces = []
    errors = []
    for ns in reversed(namespace_labels_snapshot):
        try:
            result = await dispatch_agent_job(
                command="istio_migrate_namespace",
                parameters={
                    "namespace": ns,
                    "source_rev": target_rev,
                    "target_rev": source_rev,
                    "kubeconfig_path": kubeconfig_path,
                },
                asset_ids=[asset_id],
                timeout_seconds=600,
            )
            if result.get("success", True):
                rolled_back_namespaces.append(ns)
            else:
                errors.append({"namespace": ns, "error": result.get("error")})
        except Exception as exc:
            errors.append({"namespace": ns, "error": str(exc)})

    # Remove new revision
    try:
        await dispatch_agent_job(
            command="istio_remove_revision",
            parameters={"revision": target_rev, "kubeconfig_path": kubeconfig_path},
            asset_ids=[asset_id],
            timeout_seconds=300,
        )
    except Exception as exc:
        errors.append({"phase": "remove_target_revision", "error": str(exc)})

    return {
        "rolled_back": len(errors) == 0,
        "rolled_back_namespaces": rolled_back_namespaces,
        "errors": errors,
        "note": "Namespaces restored to source revision; new revision removed.",
    }
