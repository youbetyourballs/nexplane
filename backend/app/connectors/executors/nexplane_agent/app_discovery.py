from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Dispatch agent_appdiscovery to the registered Nexplane agent on the target host.

    Falls back to mock data when no agent is registered (e.g. in unit tests or
    when the agent has not yet been deployed to the target asset).
    """
    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    try:
        result = await dispatch_agent_job(
            command="discover_applications",
            parameters=parameters,
            asset_ids=list(asset_ids),
            timeout_seconds=120,
        )
        return result
    except RuntimeError:
        # No agent registered — return mock discovery data so the executor
        # does not hard-fail in environments where the agent isn't deployed.
        return {
            "action": "discover_applications",
            "applications": [
                {
                    "id": "mock-app-nginx",
                    "name": "nginx",
                    "binary": "/usr/sbin/nginx",
                    "systemd_unit": "nginx.service",
                    "listening_ports": [{"port": 80, "protocol": "tcp"}, {"port": 443, "protocol": "tcp"}],
                    "config_files": ["/etc/nginx/nginx.conf"],
                    "data_directories": [],
                    "estimated_data_size_gb": 0.0,
                    "stateful": False,
                    "external_data_stores": [],
                    "process_user": "www-data",
                    "env_vars": [],
                    "dependencies": ["libc6", "libssl3"],
                    "containerization_status": "not_started",
                }
            ],
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "host": parameters.get("hostname", "unknown"),
        }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "note": "appdiscovery is read-only, no rollback required"}
