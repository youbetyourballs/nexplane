from __future__ import annotations

import asyncio
from datetime import datetime, timezone

SERVICE_NAME = "nexplane-agent"
AGENT_EXE = "C:\\nexplane\\nexplane-agent.exe"


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    agent_secret = parameters.get("agent_secret", "")
    control_plane_url = parameters.get("control_plane_url", "")

    if not creds:
        return {
            "action": "winrm_install_agent",
            "service_created": True,
            "service_name": SERVICE_NAME,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import get_winrm_client

    loop = asyncio.get_event_loop()

    def _install():
        client = get_winrm_client(connector)

        # Create service
        bin_path = '{} --secret "{}" --url "{}"'.format(AGENT_EXE, agent_secret, control_plane_url)
        stdout, stderr, rc = client.run_cmd(
            'sc.exe create {} binpath= "{}" start= auto'.format(SERVICE_NAME, bin_path)
        )
        if rc != 0 and "already exists" not in stderr.lower():
            raise RuntimeError("sc.exe create failed (rc={}): {}".format(rc, stderr))

        # Start service
        stdout2, stderr2, rc2 = client.run_cmd("sc.exe start {}".format(SERVICE_NAME))
        if rc2 != 0 and "already running" not in stderr2.lower():
            raise RuntimeError("sc.exe start failed (rc={}): {}".format(rc2, stderr2))

        return {"create_stdout": stdout, "start_stdout": stdout2}

    try:
        result = await loop.run_in_executor(None, _install)
    except Exception as e:
        return {
            "action": "winrm_install_agent",
            "service_created": False,
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_install_agent",
        "service_created": True,
        "service_name": SERVICE_NAME,
        "details": result,
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

    def _uninstall():
        client = get_winrm_client(connector)
        client.run_cmd("sc.exe stop {}".format(SERVICE_NAME))
        stdout, stderr, rc = client.run_cmd("sc.exe delete {}".format(SERVICE_NAME))
        return rc

    try:
        rc = await loop.run_in_executor(None, _uninstall)
        return {"rolled_back": True, "service_name": SERVICE_NAME, "delete_rc": rc}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
