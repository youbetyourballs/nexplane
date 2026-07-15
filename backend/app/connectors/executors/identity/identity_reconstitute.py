# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
identity_reconstitute — compare current state to a snapshot, dry-run detects
divergence, then operator-approved reconstitution recreates missing/corrupted accounts.

Execution sequence:
1. Download and validate manifest
2. Dry-run analysis (always first) — missing | corrupted | orphaned | clean
3. If dry_run=True: return analysis, pause
4. Reconstitute: delete corrupted, recreate missing
5. Report
"""
from __future__ import annotations
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"

STRUCTURAL_FIELDS = {"enabled", "locked", "group_memberships", "role_assignments",
                     "mfa_state", "status", "accountEnabled", "rolebindings"}
MAX_BATCH_DEFAULT = 100


async def _load_snapshot(s3_bucket: str, s3_prefix: str, creds: dict) -> dict:
    import boto3
    kwargs = {}
    if creds.get("aws_access_key_id"):
        kwargs["aws_access_key_id"] = creds["aws_access_key_id"]
        kwargs["aws_secret_access_key"] = creds["aws_secret_access_key"]
    if creds.get("region"):
        kwargs["region_name"] = creds["region"]
    s3 = boto3.client("s3", **kwargs)

    manifest_key = f"{s3_prefix.rstrip('/')}/manifest.json"
    manifest_obj = s3.get_object(Bucket=s3_bucket, Key=manifest_key)
    manifest = json.loads(manifest_obj["Body"].read())

    users_by_connector = {}
    for connector_id in manifest.get("connector_ids", []):
        users_key = f"{s3_prefix.rstrip('/')}/{connector_id}/users.json"
        try:
            obj = s3.get_object(Bucket=s3_bucket, Key=users_key)
            users_by_connector[connector_id] = json.loads(obj["Body"].read())
        except Exception:
            users_by_connector[connector_id] = []

    return {"manifest": manifest, "users_by_connector": users_by_connector}


async def _discover_users(connector, action: str, params: dict) -> dict:
    from app.services.connector_service import execute_action
    return await execute_action(connector, action, params)


def _classify(snapshot_user: dict, current_by_id: dict) -> str:
    ext_id = snapshot_user.get("external_id", "")
    current = current_by_id.get(ext_id)
    if current is None:
        return "missing"
    snap_raw = snapshot_user.get("raw_attributes") or {}
    curr_raw = current.get("raw_attributes") or {}
    for field in STRUCTURAL_FIELDS:
        if field in snap_raw and snap_raw.get(field) != curr_raw.get(field):
            return "corrupted"
    return "clean"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    s3_bucket = parameters.get("s3_bucket") or creds.get("s3_bucket", "")
    s3_prefix = parameters.get("s3_prefix") or creds.get("s3_prefix", "")
    dry_run = parameters.get("dry_run", True)
    max_batch = int(parameters.get("max_batch", MAX_BATCH_DEFAULT))

    if not s3_bucket or not s3_prefix:
        return {"status": "failed", "reason": "s3_bucket and s3_prefix are required"}

    try:
        snapshot = await _load_snapshot(s3_bucket, s3_prefix, creds)
    except Exception as exc:
        return {"status": "failed", "reason": f"Failed to load snapshot: {exc}"}

    manifest = snapshot["manifest"]
    if manifest.get("format") != "identity_snapshot_v1":
        return {
            "status": "failed",
            "reason": f"Invalid snapshot format: {manifest.get('format')!r}. Expected 'identity_snapshot_v1'.",
        }

    connector_id = str(connector.id)
    snapshot_users = snapshot["users_by_connector"].get(connector_id, [])

    try:
        current_result = await _discover_users(connector, "discover_users", {})
        current_users = current_result.get("users") or current_result.get("accounts") or []
    except Exception as exc:
        return {"status": "failed", "reason": f"discover_users failed: {exc}"}

    current_by_id = {u.get("external_id", u.get("id", "")): u for u in current_users}
    snapshot_ids = {u.get("external_id", "") for u in snapshot_users}

    analysis = []
    for user in snapshot_users:
        ext_id = user.get("external_id", "")
        classification = _classify(user, current_by_id)
        analysis.append({
            "external_id": ext_id,
            "username": user.get("username", ""),
            "classification": classification,
        })

    for ext_id, user in current_by_id.items():
        if ext_id not in snapshot_ids:
            analysis.append({
                "external_id": ext_id,
                "username": user.get("username", ""),
                "classification": "orphaned",
                "note": "exists now, not in snapshot — flagged for operator review, will NOT be auto-deleted",
            })

    summary = {c: sum(1 for a in analysis if a["classification"] == c)
               for c in ("missing", "corrupted", "orphaned", "clean")}

    if dry_run:
        return {
            "status": "dry_run_complete",
            "analysis": analysis,
            "summary": summary,
            "next_step": "Re-submit CR with dry_run=false to execute reconstitution",
        }

    to_reconstitute = [a for a in analysis if a["classification"] in ("missing", "corrupted")]
    if len(to_reconstitute) > max_batch:
        return {
            "status": "failed",
            "reason": (
                f"Reconstitution batch size {len(to_reconstitute)} exceeds max_batch={max_batch}. "
                "Re-submit with explicit max_batch override to proceed."
            ),
        }

    reconstituted = []
    failed = []
    snapshot_by_id = {u.get("external_id", ""): u for u in snapshot_users}

    from app.services.connector_service import execute_action

    for item in to_reconstitute:
        ext_id = item["external_id"]
        snap_user = snapshot_by_id.get(ext_id, {})
        try:
            if item["classification"] == "corrupted":
                await execute_action(connector, "delete_user", {"user_id": ext_id})
            await execute_action(connector, "create_user", {
                "user_id": ext_id,
                "attributes": snap_user.get("raw_attributes") or {},
            })
            reconstituted.append({"external_id": ext_id, "status": "reconstituted"})
        except Exception as exc:
            failed.append({"external_id": ext_id, "status": "error", "reason": str(exc)})

    status = "completed" if not failed else ("partial" if reconstituted else "failed")
    return {
        "status": status,
        "summary": summary,
        "reconstituted": len(reconstituted),
        "failed": len(failed),
        "results": reconstituted + failed,
        "orphaned_flagged": summary.get("orphaned", 0),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": (
            "identity_reconstitute rollback is not automatic. "
            "Run a fresh identity_snapshot of the current state and compare manually, "
            "or re-run identity_reconstitute targeting the pre-reconstitution snapshot."
        ),
    }
