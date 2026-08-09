# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Istio control plane upgrade executor.

Uses run_command exclusively (inplace strategy via istioctl upgrade).

Flow: preflight -> dry-run (optional) -> istioctl upgrade -> rollout wait -> verify.

Rollback: download old istio binary -> istioctl install --set profile=minimal.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":   p.get("source_version", ""),
        "target_version":   p.get("target_version", ""),
        "kubeconfig_path":  p.get("kubeconfig_path", "/etc/rancher/k3s/k3s.yaml"),
        "upgrade_strategy": p.get("upgrade_strategy", "inplace"),
        "dry_run":          bool(p.get("dry_run", False)),
    }


async def _run(command: str, asset_id: str, timeout: int = 120) -> dict:
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    return await dispatch_agent_job(
        command="run_command",
        parameters={"command": command, "timeout": timeout},
        asset_ids=[asset_id],
        timeout_seconds=timeout + 30,
    )


_ISTIOCTL = "/usr/local/bin/istioctl"
_KUBECTL = "/usr/local/bin/k3s kubectl"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)
    source_version = p["source_version"]
    target_version = p["target_version"]
    kc = p["kubeconfig_path"]
    dry_run = p["dry_run"]

    if not source_version:
        raise ValueError("source_version required (e.g. '1.20')")
    if not target_version:
        raise ValueError("target_version required (e.g. '1.21')")

    # Validate +1 minor only
    try:
        src_minor = int(source_version.split(".")[1])
        tgt_minor = int(target_version.split(".")[1])
    except (IndexError, ValueError):
        raise ValueError("source_version and target_version must be 'MAJOR.MINOR' format")
    if tgt_minor != src_minor + 1:
        raise ValueError(
            f"Istio only supports +1 minor upgrades; got {source_version}->{target_version}"
        )

    # Step 0a: Wait for k3s kubeconfig (k3s service may still be starting from AMI)
    r = await _run(
        f"for i in $(seq 1 120); do "
        f"[ -f {kc} ] && echo KUBECONFIG_OK && break; "
        f"[ $i -eq 24 ] && systemctl status k3s 2>&1 | head -5 && systemctl start k3s 2>/dev/null || true; "
        f"sleep 5; done; "
        f"[ -f {kc} ] || echo KUBECONFIG_MISSING",
        asset_id, timeout=630,
    )
    if "KUBECONFIG_MISSING" in r.get("output", "") or "KUBECONFIG_OK" not in r.get("output", ""):
        return {
            "status": "failed",
            "phase": "preflight",
            "error": f"k3s kubeconfig not found at {kc} after 600s",
            "source_version": source_version,
            "target_version": target_version,
            "asset_id": asset_id,
        }

    # Step 0b: Download target istioctl binary
    logger.info("Downloading istioctl %s on %s", target_version, asset_id)
    r = await _run(
        f"cd /tmp && curl -sfL https://istio.io/downloadIstio | ISTIO_VERSION={target_version}.0 TARGET_ARCH=x86_64 sh - 2>&1; "
        f"echo DOWNLOAD_EXIT=$?",
        asset_id, timeout=300,
    )
    download_output = r.get("output", "")
    istioctl = f"/tmp/istio-{target_version}.0/bin/istioctl"
    if "DOWNLOAD_EXIT=0" not in download_output:
        return {
            "status": "failed",
            "phase": "download_istioctl",
            "source_version": source_version,
            "target_version": target_version,
            "download_output": download_output[:1000],
            "asset_id": asset_id,
        }

    # Step 1: Preflight - capture current version
    logger.info("Istio upgrade preflight: %s->%s on %s", source_version, target_version, asset_id)
    r = await _run(
        f"KUBECONFIG={kc} {istioctl} version 2>&1 || KUBECONFIG={kc} {_ISTIOCTL} version 2>&1 || echo 'no-istio-running'",
        asset_id, timeout=60,
    )
    preflight_output = r.get("output", "")
    logger.info("Preflight output: %s", preflight_output[:300])

    if dry_run:
        r2 = await _run(
            f"KUBECONFIG={kc} {istioctl} upgrade --set profile=minimal -y --dry-run 2>&1",
            asset_id, timeout=120,
        )
        return {
            "status": "dry_run",
            "source_version": source_version,
            "target_version": target_version,
            "preflight_output": preflight_output[:1000],
            "dry_run_output": r2.get("output", "")[:1000],
            "asset_id": asset_id,
        }

    # Step 2: Upgrade
    logger.info("Running istioctl upgrade on %s", asset_id)
    r = await _run(
        f"KUBECONFIG={kc} {istioctl} upgrade --set profile=minimal -y 2>&1; echo ISTIO_UPGRADE_EXIT=$?",
        asset_id, timeout=600,
    )
    upgrade_output = r.get("output", "")
    if "ISTIO_UPGRADE_EXIT=0" not in upgrade_output:
        return {
            "status": "failed",
            "phase": "upgrade",
            "source_version": source_version,
            "target_version": target_version,
            "upgrade_output": upgrade_output[:2000],
            "asset_id": asset_id,
        }

    # Step 3: Wait for rollout
    logger.info("Waiting for istiod rollout on %s", asset_id)
    r = await _run(
        f"{_KUBECTL} --kubeconfig={kc} rollout status deployment/istiod -n istio-system --timeout=300s 2>&1; echo ROLLOUT_EXIT=$?",
        asset_id, timeout=360,
    )
    rollout_output = r.get("output", "")
    if "ROLLOUT_EXIT=0" not in rollout_output:
        logger.warning("Rollout wait non-zero: %s", rollout_output[:300])

    # Step 4: Verify - check target version present
    r = await _run(f"KUBECONFIG={kc} {istioctl} version 2>&1", asset_id, timeout=60)
    verify_output = r.get("output", "")
    version_ok = target_version in verify_output

    verify_result = {
        "version_ok": version_ok,
        "version_output": verify_output[:500],
    }

    return {
        "status": "completed" if version_ok else "verify_failed",
        "source_version": source_version,
        "target_version": target_version,
        "upgrade_output": upgrade_output[:2000],
        "rollout_output": rollout_output[:500],
        "verify_result": verify_result,
        "asset_id": asset_id,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    p_outer = parameters.get("desired_outcome") or parameters
    asset_id = execution_result.get("asset_id") or str(
        (
            parameters.get("asset_ids")
            or parameters.get("target_asset_ids")
            or p_outer.get("asset_ids")
            or [None]
        )[0] or ""
    )
    if not asset_id:
        return {"rolled_back": False, "reason": "No asset_id available for rollback"}

    p = _resolve_params(parameters)
    source_version = p["source_version"] or execution_result.get("source_version", "")
    kc = p["kubeconfig_path"]

    if not source_version:
        return {"rolled_back": False, "reason": "source_version missing - cannot determine binary to reinstall"}

    logger.info("Istio rollback: reinstalling %s on %s", source_version, asset_id)

    # Step 1: Download old istio binary
    r = await _run(
        f"cd /tmp && curl -sfL https://istio.io/downloadIstio | ISTIO_VERSION={source_version}.0 TARGET_ARCH=x86_64 sh - 2>&1",
        asset_id, timeout=300,
    )
    download_output = r.get("output", "")

    # Step 2: Install old version
    r = await _run(
        f"KUBECONFIG={kc} /tmp/istio-{source_version}.0/bin/istioctl install --set profile=minimal -y 2>&1; echo ROLLBACK_EXIT=$?",
        asset_id, timeout=600,
    )
    rollback_output = r.get("output", "")
    rolled_back = "ROLLBACK_EXIT=0" in rollback_output

    return {
        "rolled_back": rolled_back,
        "strategy": "binary_reinstall",
        "source_version": source_version,
        "download_output": download_output[:500],
        "rollback_output": rollback_output[:1000],
        "data_loss_warning": (
            "Istio control plane reinstalled from binary. Sidecar proxies will reconnect "
            "to the restored istiod. Verify application traffic after rollback."
        ),
    }
