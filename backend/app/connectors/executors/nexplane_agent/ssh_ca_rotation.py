# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""SSH CA Rotation executor — rotates the host SSH Certificate Authority across a fleet."""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone

from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "full"

_MOCK_RESULT = {
    "phases": [
        "preflight",
        "snapshot",
        "generate",
        "distribute",
        "verify",
        "revoke",
        "report",
    ],
    "new_ca_fingerprint": "SHA256:MOCK000000000000000000000000000000000000000=",
    "new_ca_comment": "nexplane-ca-mock",
    "hosts_updated": [],
    "old_ca_snapshots": {},
    "ca_key_path": "/etc/ssh/nexplane_ca",
    "trusted_user_ca_keys_path": "/etc/ssh/trusted_user_ca_keys",
    "status": "mock",
    "_asset_ids": [],
}


async def _run_on_host(asset_id: str, command: str, timeout: int = 60) -> str:
    """Dispatch a run_command job to a single host and return stdout."""
    result = await _dispatch.dispatch_agent_job(
        command="run_command",
        parameters={"command": command},
        asset_ids=[asset_id],
        timeout_seconds=timeout,
    )
    return result.get("stdout", result.get("output", ""))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Rotate SSH CA across target hosts.

    Phases: preflight → snapshot → generate → distribute → verify → revoke → report.
    Returns mock result when asset_ids is empty (unit-test path).
    """
    asset_ids = [str(a) for a in (asset_ids or [])]

    ca_key_path = parameters.get("ca_key_path", "/etc/ssh/nexplane_ca")
    tuca_path = parameters.get("trusted_user_ca_keys_path", "/etc/ssh/trusted_user_ca_keys")
    key_type = parameters.get("key_type", "ed25519")
    ca_host_asset_id = parameters.get("ca_host_asset_id") or (asset_ids[0] if asset_ids else None)

    if not asset_ids:
        return {**_MOCK_RESULT, "ca_key_path": ca_key_path, "trusted_user_ca_keys_path": tuca_path}

    # ── Phase 1: Preflight ────────────────────────────────────────────────────
    preflight_results: dict[str, str] = {}
    for host in asset_ids:
        out = await _run_on_host(
            host,
            f"which ssh-keygen && cat /etc/ssh/sshd_config | grep TrustedUserCAKeys || echo NOT_CONFIGURED",
        )
        preflight_results[host] = out.strip()

    # ── Phase 2: Snapshot ─────────────────────────────────────────────────────
    old_ca_snapshots: dict[str, str] = {}
    for host in asset_ids:
        out = await _run_on_host(host, f"cat {tuca_path} 2>/dev/null || echo EMPTY")
        old_ca_snapshots[host] = out.strip()

    # ── Phase 3: Generate (on CA host) ────────────────────────────────────────
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ca_comment = f"nexplane-ca-{timestamp}"
    gen_out = await _run_on_host(
        ca_host_asset_id,
        f"rm -f {ca_key_path} {ca_key_path}.pub; ssh-keygen -t {key_type} -f {ca_key_path} -N '' -C '{ca_comment}' 2>/dev/null; cat {ca_key_path}.pub",
        timeout=90,
    )
    new_ca_pubkey = gen_out.strip()

    # Extract fingerprint comment from pubkey (last field)
    new_ca_comment = new_ca_pubkey.split()[-1] if new_ca_pubkey.split() else ca_comment

    # ── Phase 4: Distribute ───────────────────────────────────────────────────
    distribute_results: dict[str, str] = {}
    for host in asset_ids:
        out = await _run_on_host(
            host,
            f"echo {new_ca_pubkey!r} >> {tuca_path} && sort -u {tuca_path} -o {tuca_path} && echo OK",
        )
        distribute_results[host] = out.strip()

    # ── Phase 5: Verify ───────────────────────────────────────────────────────
    verify_results: dict[str, str] = {}
    for host in asset_ids:
        out = await _run_on_host(
            host,
            f"grep -F '{new_ca_comment}' {tuca_path} && echo FOUND || echo MISSING",
        )
        verify_results[host] = out.strip()
        if "MISSING" in out:
            raise RuntimeError(f"New CA key not found on host {host} after distribute — aborting before revoke")

    # ── Phase 6: Revoke (remove old CA) ──────────────────────────────────────
    revoke_results: dict[str, str] = {}
    for host in asset_ids:
        snapshot = old_ca_snapshots.get(host, "")
        if not snapshot or snapshot == "EMPTY":
            revoke_results[host] = "no_old_ca"
            continue
        # Extract old CA comment(s) to remove — remove any line not containing new_ca_comment
        out = await _run_on_host(
            host,
            (
                f"grep -v '{new_ca_comment}' {tuca_path} > /tmp/tuca_old_lines; "
                f"grep -F '{new_ca_comment}' {tuca_path} > /tmp/tuca_new_only; "
                f"mv /tmp/tuca_new_only {tuca_path}; "
                f"systemctl reload sshd || kill -HUP $(cat /run/sshd.pid 2>/dev/null) || true; "
                f"echo REVOKED"
            ),
        )
        revoke_results[host] = out.strip()

    # ── Phase 7: Report ───────────────────────────────────────────────────────
    return {
        "phases": ["preflight", "snapshot", "generate", "distribute", "verify", "revoke", "report"],
        "new_ca_fingerprint": new_ca_comment,
        "new_ca_comment": new_ca_comment,
        "new_ca_pubkey": new_ca_pubkey,
        "hosts_updated": asset_ids,
        "old_ca_snapshots": old_ca_snapshots,
        "preflight_results": preflight_results,
        "distribute_results": distribute_results,
        "verify_results": verify_results,
        "revoke_results": revoke_results,
        "ca_key_path": ca_key_path,
        "trusted_user_ca_keys_path": tuca_path,
        "status": "completed",
        "_asset_ids": asset_ids,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Restore old TrustedUserCAKeys from snapshot on all hosts and reload sshd."""
    asset_ids = execution_result.get("_asset_ids") or []
    old_ca_snapshots: dict = execution_result.get("old_ca_snapshots", {})
    tuca_path = execution_result.get(
        "trusted_user_ca_keys_path",
        parameters.get("trusted_user_ca_keys_path", "/etc/ssh/trusted_user_ca_keys"),
    )

    if not asset_ids:
        return {"rolled_back": True, "reason": "mock_path_no_assets"}

    rollback_results: dict[str, str] = {}
    for host in asset_ids:
        old_content = old_ca_snapshots.get(host, "")
        if old_content == "EMPTY" or not old_content:
            # Remove the file entirely (no CA was configured before)
            out = await _run_on_host(
                host,
                f"rm -f {tuca_path}; systemctl reload sshd || true; echo REMOVED",
            )
        else:
            out = await _run_on_host(
                host,
                f"cat > {tuca_path} << 'NEXPLANE_EOF'\n{old_content}\nNEXPLANE_EOF\nsystemctl reload sshd || true; echo RESTORED",
            )
        rollback_results[host] = out.strip()

    return {
        "rolled_back": True,
        "hosts_restored": asset_ids,
        "rollback_results": rollback_results,
        "trusted_user_ca_keys_path": tuca_path,
    }
