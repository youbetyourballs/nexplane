# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Read and parse authorized_keys files on SSH-managed assets."""
from __future__ import annotations
import asyncio
import re
from ._client import get_ssh_client, prepare_ssh_target

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _parse_authorized_keys(raw: str) -> list[dict]:
    """Parse raw authorized_keys content into structured key entries."""
    keys = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        key_type = parts[0]
        key_material = parts[1]
        comment = " ".join(parts[2:]) if len(parts) > 2 else ""
        m = _DATE_RE.search(comment)
        keys.append({
            "key_type": key_type,
            "key_fingerprint": key_material[:14],
            "comment": comment,
            "added_date": m.group(1) if m else None,
        })
    return keys


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {"keys": [], "host": "mock", "mock": True}

    creds = await prepare_ssh_target(connector, creds)
    loop = asyncio.get_event_loop()

    def _run(asset_id: str) -> dict:
        client = get_ssh_client(creds)
        try:
            cmd = "cat /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys 2>/dev/null || true"
            _, stdout, _ = client.exec_command(cmd, timeout=15)
            stdout.channel.recv_exit_status()
            raw = stdout.read().decode(errors="replace")
            return {
                "asset_id": asset_id,
                "host": creds.get("hostname", "unknown"),
                "keys": _parse_authorized_keys(raw),
            }
        finally:
            client.close()

    results = []
    for asset_id in asset_ids:
        try:
            results.append(await loop.run_in_executor(None, _run, str(asset_id)))
        except Exception as e:
            results.append({"asset_id": str(asset_id), "keys": [], "error": str(e)})

    all_keys = []
    for r in results:
        all_keys.extend(r.get("keys", []))

    return {
        "keys": all_keys,
        "host": creds.get("hostname", "unknown"),
        "host_results": results,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "authorized_keys_audit is read-only"}
