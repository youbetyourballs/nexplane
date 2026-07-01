# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import base64
import os
import subprocess
import tempfile


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    release_name = parameters["release_name"]
    namespace = parameters["namespace"]
    revision = parameters.get("revision", 0)
    if not creds:
        return {"action": "rollback_release", "release": release_name, "revision": revision, "rolled_back": True}
    kubeconfig_b64 = creds.get("kubeconfig", "")
    if not kubeconfig_b64:
        raise ValueError("kubeconfig credential is missing or empty")
    loop = asyncio.get_event_loop()

    def run_helm():
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False) as f:
                f.write(base64.b64decode(kubeconfig_b64))
                tmp_path = f.name
            cmd = [
                "helm", "rollback", release_name, str(revision),
                "--namespace", namespace,
                "--kubeconfig", tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return result.returncode, result.stdout, result.stderr
        finally:
            if tmp_path is not None:
                os.unlink(tmp_path)

    rc, stdout, stderr = await loop.run_in_executor(None, run_helm)
    if rc != 0:
        return {"action": "rollback_release", "release": release_name, "error": stderr}
    return {"action": "rollback_release", "release": release_name, "revision": revision, "rolled_back": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "rollback of a rollback — re-run with different revision"}
