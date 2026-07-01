# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

AGENT_DEST = "C:\\nexplane\\nexplane-agent.exe"


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    agent_url = parameters.get("agent_url", "")

    if not creds:
        return {
            "action": "winrm_download_agent",
            "downloaded": True,
            "destination": AGENT_DEST,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    if not agent_url:
        raise ValueError("agent_url parameter is required")

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _download():
        client = get_winrm_client(connector)
        # Create directory
        client.run_ps("New-Item -ItemType Directory -Force -Path 'C:\\nexplane'")
        # Download
        script = (
            "Invoke-WebRequest -Uri '{}' -OutFile '{}' -UseBasicParsing".format(
                agent_url, AGENT_DEST
            )
        )
        stdout, stderr, rc = client.run_ps(script)
        if rc != 0:
            raise RuntimeError("Download failed (rc={}): {}".format(rc, stderr))
        return stdout.strip()

    try:
        output = await loop.run_in_executor(None, _download)
    except Exception as e:
        return {
            "action": "winrm_download_agent",
            "downloaded": False,
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_download_agent",
        "downloaded": True,
        "destination": AGENT_DEST,
        "output": output,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {"rolled_back": True, "reason": "mock — nothing to remove"}

    from ._client import get_winrm_client
    import asyncio as _asyncio

    loop = _asyncio.get_event_loop()

    def _remove():
        client = get_winrm_client(connector)
        stdout, stderr, rc = client.run_ps(
            "Remove-Item '{}' -Force -ErrorAction SilentlyContinue".format(AGENT_DEST)
        )
        return rc

    try:
        rc = await loop.run_in_executor(None, _remove)
        return {"rolled_back": True, "destination": AGENT_DEST, "rc": rc}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
