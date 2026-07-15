# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import logging

from app.connectors.executors.nexplane_agent import _dispatch

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # --- Kernel/capability preflight diagnostics ---
    # Run audit_ebpf_posture before the main configure so we capture the kernel
    # state regardless of whether configure succeeds or fails.  Failures here are
    # non-fatal.
    diagnostics: dict = {
        "kernel_version": None,
        "bpf_syscall_available": False,
        "cap_bpf_present": False,
        "cap_net_admin_present": False,
        "bpf_prog_types_supported": [],
        "tc_qdisc_available": False,
        "kprobe_available": False,
    }
    try:
        posture = await _dispatch.dispatch_agent_job(
            command="audit_ebpf_posture",
            parameters={},
            asset_ids=list(asset_ids),
            timeout_seconds=60,
        )
        diagnostics.update({
            "kernel_version": posture.get("kernel_version"),
            "bpf_syscall_available": posture.get("bpf_syscall_available", False),
            "cap_bpf_present": posture.get("cap_bpf_present", False),
            "cap_net_admin_present": posture.get("cap_net_admin_present", False),
            "bpf_prog_types_supported": posture.get("bpf_prog_types_supported", []),
            "tc_qdisc_available": posture.get("tc_qdisc_available", False),
            "kprobe_available": posture.get("kprobe_available", False),
        })
        logger.info(
            "eBPF LSM preflight diagnostics: kernel=%s bpf_syscall=%s cap_bpf=%s "
            "cap_net_admin=%s tc_qdisc=%s kprobe=%s prog_types=%s",
            diagnostics["kernel_version"],
            diagnostics["bpf_syscall_available"],
            diagnostics["cap_bpf_present"],
            diagnostics["cap_net_admin_present"],
            diagnostics["tc_qdisc_available"],
            diagnostics["kprobe_available"],
            diagnostics["bpf_prog_types_supported"],
        )
    except Exception as diag_err:
        logger.warning("eBPF LSM preflight probe failed (non-fatal): %s", diag_err)
        diagnostics["preflight_error"] = str(diag_err)

    result = await _dispatch.dispatch_agent_job(
        command="configure_ebpf_lsm",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["ebpf_diagnostics"] = diagnostics
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_ebpf_lsm",
        parameters={"action": "restore", "snapshot_id": execution_result.get("snapshot_id", "")},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
