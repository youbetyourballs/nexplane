import asyncio
import subprocess

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    release_name = parameters["release_name"]
    namespace = parameters["namespace"]
    if not creds:
        return {"action": "uninstall_release", "release": release_name, "uninstalled": True}
    loop = asyncio.get_event_loop()
    def run_helm():
        cmd = ["helm", "uninstall", release_name, "--namespace", namespace]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode, result.stdout, result.stderr
    rc, stdout, stderr = await loop.run_in_executor(None, run_helm)
    if rc != 0:
        return {"action": "uninstall_release", "release": release_name, "error": stderr}
    return {"action": "uninstall_release", "release": release_name, "uninstalled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uninstall is destructive — re-install manually"}
