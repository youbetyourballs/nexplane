import asyncio
import base64
import os
import subprocess
import tempfile


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    release_name = parameters["release_name"]
    namespace = parameters["namespace"]
    if not creds:
        return {"action": "uninstall_release", "release": release_name, "uninstalled": True}
    kubeconfig_b64 = creds.get("kubeconfig", "")
    loop = asyncio.get_event_loop()

    def run_helm():
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False) as f:
            f.write(base64.b64decode(kubeconfig_b64))
            tmp_path = f.name
        try:
            cmd = [
                "helm", "uninstall", release_name,
                "--namespace", namespace,
                "--kubeconfig", tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return result.returncode, result.stdout, result.stderr
        finally:
            os.unlink(tmp_path)

    rc, stdout, stderr = await loop.run_in_executor(None, run_helm)
    if rc != 0:
        return {"action": "uninstall_release", "release": release_name, "error": stderr}
    return {"action": "uninstall_release", "release": release_name, "uninstalled": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uninstall is destructive — re-install manually"}
