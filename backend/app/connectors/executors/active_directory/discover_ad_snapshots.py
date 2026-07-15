# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
discover_ad_snapshots — list available AD forest snapshots stored in S3.

Groups S3 objects by snapshot_id (timestamp-based folder under the prefix),
returns each snapshot with its artifact inventory, total size, and age.

Snapshot layout produced by ad_forest_snapshot:
  {prefix}/{snapshot_id}/ntds.dit
  {prefix}/{snapshot_id}/SYSVOL.zip        (if include_sysvol was True)
  {prefix}/{snapshot_id}/GPO-backup.zip    (if GPO backup succeeded)

snapshot_id is the timestamp folder name, e.g. "20240315T143022Z".
"""

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


def _do_discover(s3_bucket: str, s3_prefix: str) -> list[dict]:
    import boto3

    s3 = boto3.client("s3")

    # Normalise prefix — ensure it ends with /
    prefix = s3_prefix.rstrip("/") + "/"

    paginator = s3.get_paginator("list_objects_v2")
    snapshots: dict[str, dict] = {}  # snapshot_id -> aggregated data

    for page in paginator.paginate(Bucket=s3_bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key: str = obj["Key"]
            # Strip the leading prefix to get relative path: {snapshot_id}/{artifact}
            relative = key[len(prefix):]
            parts = relative.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                # Top-level object under prefix — not a snapshot artifact
                continue
            snapshot_id, artifact_name = parts[0], parts[1]

            if snapshot_id not in snapshots:
                snapshots[snapshot_id] = {
                    "snapshot_id": snapshot_id,
                    "artifacts": [],
                    "total_size_bytes": 0,
                    "last_modified": None,
                }

            entry = snapshots[snapshot_id]
            size = obj.get("Size", 0)
            last_mod: datetime = obj.get("LastModified")  # timezone-aware datetime from boto3

            entry["artifacts"].append(
                {
                    "name": artifact_name,
                    "size_bytes": size,
                    "s3_key": key,
                    "last_modified": last_mod.isoformat() if last_mod else None,
                }
            )
            entry["total_size_bytes"] += size

            # Track the earliest LastModified as the snapshot timestamp
            if last_mod and (entry["last_modified"] is None or last_mod < entry["last_modified"]):
                entry["last_modified"] = last_mod

    now = datetime.now(timezone.utc)
    result = []
    for snap_id, data in snapshots.items():
        last_mod_dt: datetime | None = data.pop("last_modified", None)
        timestamp_iso = last_mod_dt.isoformat() if last_mod_dt else None
        age_days: float | None = None
        if last_mod_dt:
            age_days = round((now - last_mod_dt).total_seconds() / 86400, 1)

        # Sort artifacts by name for determinism
        data["artifacts"].sort(key=lambda a: a["name"])

        result.append(
            {
                "snapshot_id": snap_id,
                "timestamp": timestamp_iso,
                "artifacts": data["artifacts"],
                "total_size_bytes": data["total_size_bytes"],
                "age_days": age_days,
                "s3_bucket": s3_bucket,
                "s3_prefix": f"{prefix}{snap_id}",
                # Convenience key for use as snapshot_s3_key in ad_forest_restore
                "ntds_s3_key": f"{prefix}{snap_id}/ntds.dit"
                if any(a["name"] == "ntds.dit" for a in data["artifacts"])
                else None,
            }
        )

    # Sort newest first (by snapshot_id descending — they are timestamp strings)
    result.sort(key=lambda s: s["snapshot_id"], reverse=True)
    return result


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    s3_bucket = parameters["s3_bucket"]
    s3_prefix = parameters.get("s3_prefix", "ad-snapshots/")

    loop = asyncio.get_event_loop()
    snapshots = await loop.run_in_executor(
        None, lambda: _do_discover(s3_bucket, s3_prefix)
    )

    return {
        "s3_bucket": s3_bucket,
        "s3_prefix": s3_prefix,
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
    }
