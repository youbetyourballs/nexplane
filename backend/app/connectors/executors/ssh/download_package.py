import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    agent_type = parameters.get("agent_type", "nexplane")
    agent_version = parameters.get("agent_version", "latest")
    download_url = parameters.get("download_url", "")
    dest_path = parameters.get("dest_path", "/tmp/nexplane-agent")

    if not download_url:
        return {"status": "error", "message": "download_url is required"}

    if not creds:
        return {
            "action": "download_package",
            "agent_type": agent_type,
            "agent_version": agent_version,
            "dest_path": dest_path,
            "hosts": [{"asset_id": a, "downloaded": True} for a in asset_ids],
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_ssh_client
    loop = asyncio.get_event_loop()
    host_results = []

    def _download(asset_id: str):
        client = get_ssh_client(creds)
        try:
            cmd = f"curl -fsSL '{download_url}' -o '{dest_path}' && chmod +x '{dest_path}'"
            _, stdout, stderr = client.exec_command(cmd, timeout=120)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                return {"asset_id": asset_id, "downloaded": False, "error": stderr.read().decode()}
            return {"asset_id": asset_id, "downloaded": True, "dest_path": dest_path}
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _download, str(asset_id))
        except Exception as exc:
            result = {"asset_id": str(asset_id), "downloaded": False, "error": str(exc)}
        host_results.append(result)

    return {
        "action": "download_package",
        "agent_type": agent_type,
        "agent_version": agent_version,
        "hosts": host_results,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "download has no rollback"}
