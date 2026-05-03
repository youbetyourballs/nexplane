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
    revision = parameters.get("revision")  # None = one revision back

    if not creds:
        return {
            "action": "helm_rollback",
            "namespace": namespace,
            "release_name": release_name,
            "revision": revision,
            "rolled_back": True,
            "mock": True,
        }

    loop = asyncio.get_event_loop()

    def _call():
        kubeconfig_b64 = creds.get("kubeconfig_b64") or creds.get("kubeconfig")
        kubeconfig_bytes = base64.b64decode(kubeconfig_b64)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as kf:
            kf.write(kubeconfig_bytes)
            kubeconfig_path = kf.name

        try:
            history_proc = subprocess.run(
                ["helm", "history", release_name, "--namespace", namespace,
                 "--kubeconfig", kubeconfig_path, "--output", "json"],
                capture_output=True, text=True,
            )
            current_revision = None
            if history_proc.returncode == 0:
                history = json.loads(history_proc.stdout)
                if history:
                    current_revision = history[-1].get("revision")

            cmd = ["helm", "rollback", release_name, "--namespace", namespace,
                   "--kubeconfig", kubeconfig_path, "--wait"]
            if revision is not None:
                cmd.append(str(revision))

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"helm rollback failed: {proc.stderr}")
            return current_revision
        finally:
            os.unlink(kubeconfig_path)

    current_revision = await loop.run_in_executor(None, _call)
    return {
        "action": "helm_rollback",
        "namespace": namespace,
        "release_name": release_name,
        "revision": revision,
        "rolled_back": True,
        "rollback_data": {"rolled_back_from_revision": current_revision},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Re-run helm_upgrade to the version that was active before this rollback.",
        "rolled_back_from": execution_result.get("rollback_data", {}).get("rolled_back_from_revision"),
    }
