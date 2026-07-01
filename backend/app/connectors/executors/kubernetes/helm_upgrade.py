# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import base64
import json
import subprocess
import tempfile
import os
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    release_name = parameters["release_name"]
    chart = parameters["chart"]
    version = parameters.get("version")
    values = parameters.get("values", {})
    values_yaml = parameters.get("values_yaml")
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "helm_upgrade",
            "namespace": namespace,
            "release_name": release_name,
            "chart": chart,
            "upgraded": True,
            "mock": True,
        }

    loop = asyncio.get_event_loop()

    def _call():
        kubeconfig_b64 = creds.get("kubeconfig_b64") or creds.get("kubeconfig")
        kubeconfig_bytes = base64.b64decode(kubeconfig_b64)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as kf:
            kf.write(kubeconfig_bytes)
            kubeconfig_path = kf.name

        values_file = None
        try:
            history_proc = subprocess.run(
                ["helm", "history", release_name, "--namespace", namespace,
                 "--kubeconfig", kubeconfig_path, "--output", "json"],
                capture_output=True, text=True,
            )
            previous_revision = None
            if history_proc.returncode == 0:
                history = json.loads(history_proc.stdout)
                if history:
                    previous_revision = history[-1].get("revision")

            cmd = [
                "helm", "upgrade", "--install", release_name, chart,
                "--namespace", namespace,
                "--kubeconfig", kubeconfig_path,
                "--atomic", "--timeout", "5m0s",
                "--output", "json",
            ]
            if version:
                cmd += ["--version", version]
            for k, v in values.items():
                cmd += ["--set", f"{k}={v}"]
            if values_yaml:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml", mode="w") as vf:
                    vf.write(values_yaml)
                    values_file = vf.name
                cmd += ["--values", values_file]
            if dry_run:
                cmd.append("--dry-run")

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"helm upgrade failed: {proc.stderr}")
            return {"previous_revision": previous_revision, "helm_output": proc.stdout}
        finally:
            os.unlink(kubeconfig_path)
            if values_file:
                os.unlink(values_file)

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "helm_upgrade",
        "namespace": namespace,
        "release_name": release_name,
        "chart": chart,
        "dry_run": dry_run,
        "upgraded": True,
        "rollback_data": {"previous_revision": result["previous_revision"]},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.kubernetes.helm_rollback import execute as helm_rb
    revision = execution_result.get("rollback_data", {}).get("previous_revision")
    rollback_params = {
        "namespace": parameters["namespace"],
        "release_name": parameters["release_name"],
        "revision": revision,
    }
    return await helm_rb(rollback_params, [], connector)
