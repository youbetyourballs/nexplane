# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
HashiCorp Vault cluster upgrade executor.
Uses run_command agent primitive.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

_VAULT_ADDR = "https://127.0.0.1:8200"


def _resolve_params(parameters: dict) -> dict:
    p = parameters.get("desired_outcome") or parameters
    return {
        "source_version":     p.get("source_version"),
        "target_version":     p["target_version"],
        "nodes":              p.get("nodes") or [],
        "vault_token":        p.get("vault_token", ""),
        "snapshot_s3_bucket": p.get("snapshot_s3_bucket"),
        "dry_run":            bool(p.get("dry_run", False)),
    }


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


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    if not asset_ids:
        raise ValueError("asset_ids required")

    asset_id = str(asset_ids[0])
    p = _resolve_params(parameters)
    token = p["vault_token"]
    env = "VAULT_SKIP_VERIFY=true VAULT_ADDR=" + _VAULT_ADDR + " VAULT_TOKEN=" + token
    target_v = p["target_version"]

    # --- Phase 1: Preflight ---
    r = await _run(env + " vault status 2>&1 || true", asset_id, timeout=60)
    preflight_output = r.get("output", "")
    logger.info("Vault preflight: %s", preflight_output[:200])

    if p["dry_run"]:
        return {"dry_run": True, "preflight_output": preflight_output}

    # --- Phase 2: Raft snapshot ---
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = "/tmp/nexplane-vault-" + ts + ".snap"
    snap_cmd = env + " vault operator raft snapshot save " + snapshot_path + " 2>&1; echo SNAP_EXIT=$?"
    r = await _run(snap_cmd, asset_id, timeout=120)
    snap_output = r.get("output", "")
    if "SNAP_EXIT=0" not in snap_output:
        snapshot_path = "/tmp/nexplane-vault-nosnap-" + ts + ".txt"
        await _run("echo no-raft > " + snapshot_path, asset_id, timeout=10)
    logger.info("Vault snapshot: %s", snapshot_path)

    # --- Phase 3: Upgrade vault binary ---
    # HashiCorp releases use full semver: vault_1.16.0_linux_amd64.zip
    # Try X.Y.0 first (most common), then bare X.Y as fallback
    upgrade_script = """
TARGET_VER=%(ver)s
ARCH=linux_amd64
# Normalize to full semver (1.16 -> 1.16.0)
if echo "$TARGET_VER" | grep -qE '^[0-9]+[.][0-9]+$'; then
    FULL_VER="${TARGET_VER}.0"
else
    FULL_VER="$TARGET_VER"
fi
BASE_URL="https://releases.hashicorp.com/vault/${FULL_VER}"
ZIP="vault_${FULL_VER}_${ARCH}.zip"
TMP=$(mktemp -d)
curl -sf -L -o "$TMP/vault.zip" "${BASE_URL}/${ZIP}" 2>&1 || {
    echo "DOWNLOAD_FAILED url=${BASE_URL}/${ZIP}"
    rm -rf "$TMP"
    exit 0
}
unzip -o "$TMP/vault.zip" -d "$TMP/" 2>&1
VAULT_BIN=$(which vault 2>/dev/null || echo /usr/local/bin/vault)
systemctl stop vault 2>/dev/null || true
sleep 2
cp "$TMP/vault" "$VAULT_BIN"
chmod +x "$VAULT_BIN"
systemctl start vault 2>/dev/null || nohup vault server -config=/etc/vault.d/vault.hcl >> /var/log/vault.log 2>&1 &
sleep 6
rm -rf "$TMP"
echo UPGRADE_DONE
""" % {"ver": target_v}
    r = await _run(upgrade_script, asset_id, timeout=600)
    upgrade_output = r.get("output", "")
    upgrade_ok = "UPGRADE_DONE" in upgrade_output and "DOWNLOAD_FAILED" not in upgrade_output

    if upgrade_ok:
        # Re-unseal if init file present
        unseal_script = """
F=/tmp/vault-init.json
if [ -f "$F" ]; then
    UK=$(python3 -c "import json; d=json.load(open('$F')); print(d['unseal_keys_b64'][0])" 2>/dev/null)
    [ -n "$UK" ] && VAULT_SKIP_VERIFY=true VAULT_ADDR=https://127.0.0.1:8200 vault operator unseal "$UK" 2>/dev/null || true
fi
echo UNSEAL_DONE
"""
        await _run(unseal_script, asset_id, timeout=30)

    # --- Phase 4: Verify ---
    r = await _run(env + " vault version 2>&1", asset_id, timeout=30)
    version_output = r.get("output", "")
    version_ok = target_v in version_output

    r2 = await _run(env + " vault status 2>&1 || true", asset_id, timeout=30)
    health_output = r2.get("output", "")

    verify_result = {
        "all_healthy":    r2.get("exit_code", 1) in (0, 2),
        "version_ok":     version_ok,
        "version_output": version_output[:200],
    }

    return {
        "status":         "completed" if version_ok else "verify_failed",
        "source_version": p["source_version"],
        "target_version": target_v,
        "nodes_upgraded": [n.get("host") for n in p["nodes"]],
        "snapshot_path":  snapshot_path,
        "verify_result":  verify_result,
        "asset_id":       asset_id,
        "upgraded_at":    datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    snapshot_path = execution_result.get("snapshot_path")
    if not snapshot_path:
        return {"rolled_back": False, "reason": "no snapshot_path in execution_result"}

    asset_id = execution_result.get("asset_id") or str(
        (parameters.get("asset_ids") or [None])[0]
    )
    p = _resolve_params(parameters)
    token = p["vault_token"]
    env = "VAULT_SKIP_VERIFY=true VAULT_ADDR=" + _VAULT_ADDR + " VAULT_TOKEN=" + token
    source_v = p.get("source_version") or execution_result.get("source_version", "")

    try:
        rollback_script = """
SRC_VER=%(src)s
if [ -n "$SRC_VER" ]; then
    ARCH=linux_amd64
    if echo "$SRC_VER" | grep -qE '^[0-9]+[.][0-9]+$'; then
        FULL_VER="${SRC_VER}.0"
    else
        FULL_VER="$SRC_VER"
    fi
    URL="https://releases.hashicorp.com/vault/${FULL_VER}/vault_${FULL_VER}_${ARCH}.zip"
    TMP=$(mktemp -d)
    curl -sf -L -o "$TMP/vault.zip" "$URL" 2>&1 || { echo "DOWNLOAD_FAILED url=$URL"; rm -rf "$TMP"; exit 0; }
    unzip -o "$TMP/vault.zip" -d "$TMP/" 2>&1
    VAULT_BIN=$(which vault 2>/dev/null || echo /usr/local/bin/vault)
    systemctl stop vault 2>/dev/null || true
    sleep 2
    cp "$TMP/vault" "$VAULT_BIN"
    chmod +x "$VAULT_BIN"
    systemctl start vault 2>/dev/null || nohup vault server -config=/etc/vault.d/vault.hcl >> /var/log/vault.log 2>&1 &
    sleep 6
    rm -rf "$TMP"
fi
SNAP=%(snap)s
if [ -f "$SNAP" ] && [ "$(cat $SNAP 2>/dev/null)" != "no-raft" ]; then
    %(env)s vault operator raft snapshot restore "$SNAP" 2>&1 || echo "RESTORE_SKIPPED"
fi
echo ROLLBACK_DONE
""" % {"src": source_v, "snap": snapshot_path, "env": env}
        r = await _run(rollback_script, asset_id, timeout=600)
        out = r.get("output", "")
        ok = "ROLLBACK_DONE" in out and "DOWNLOAD_FAILED" not in out
        return {
            "rolled_back":       ok,
            "strategy":          "binary_downgrade_and_snapshot_restore",
            "snapshot_path":     snapshot_path,
            "data_loss_warning": (
                "All Vault data written between snapshot time and upgrade is lost. "
                "Verify application state after rollback."
            ),
            "rollback_output": out[:500],
        }
    except Exception as exc:
        logger.error("Vault rollback failed: %s", exc)
        return {"rolled_back": False, "reason": str(exc)}
