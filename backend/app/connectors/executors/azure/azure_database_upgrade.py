# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Azure Database flexible server version upgrade executor.

Supports PostgreSQL and MySQL flexible servers. The engine is specified
via the `engine` parameter: "postgresql" or "mysql".

Rollback is PARTIAL — Azure Database does not support version downgrades.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 30
_POLL_TIMEOUT = 3600


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _get_pg_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.rdbms.postgresql_flexibleservers import PostgreSQLManagementClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return PostgreSQLManagementClient(credential, creds["subscription_id"])


def _get_mysql_client(creds: dict):
    from azure.identity import ClientSecretCredential
    from azure.mgmt.rdbms.mysql_flexibleservers import MySQLManagementClient
    credential = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    )
    return MySQLManagementClient(credential, creds["subscription_id"])


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    resource_group = parameters["resource_group"]
    server_name = parameters["server_name"]
    target_version = parameters["target_version"]
    engine = parameters.get("engine", "postgresql").lower()

    if engine not in ("postgresql", "mysql"):
        raise ValueError(f"engine must be 'postgresql' or 'mysql', got '{engine}'")

    def _get():
        if engine == "postgresql":
            client = _get_pg_client(creds)
            return client.servers.get(resource_group, server_name)
        else:
            client = _get_mysql_client(creds)
            return client.servers.get(resource_group, server_name)

    server = await _run(_get)
    current_version = str(server.version)

    if current_version == target_version:
        return {"status": "already_at_version", "server_name": server_name,
                "engine": engine, "previous_version": current_version, "current_version": current_version}

    def _upgrade():
        if engine == "postgresql":
            from azure.mgmt.rdbms.postgresql_flexibleservers.models import ServerForUpdate
            client = _get_pg_client(creds)
            poller = client.servers.begin_update(
                resource_group, server_name, ServerForUpdate(version=target_version)
            )
        else:
            from azure.mgmt.rdbms.mysql_flexibleservers.models import ServerForUpdate
            client = _get_mysql_client(creds)
            poller = client.servers.begin_update(
                resource_group, server_name, ServerForUpdate(version=target_version)
            )
        poller.wait(timeout=60)

    await _run(_upgrade)
    logger.info("azure_database_upgrade: upgrade to %s initiated for %s (%s)", target_version, server_name, engine)

    # Poll for completion
    deadline = time.time() + _POLL_TIMEOUT
    final_version = current_version
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            if engine == "postgresql":
                return _get_pg_client(creds).servers.get(resource_group, server_name)
            return _get_mysql_client(creds).servers.get(resource_group, server_name)

        srv = await _run(_poll)
        state = getattr(srv, "state", getattr(srv, "provisioning_state", "unknown"))
        ver = str(srv.version)
        logger.info("azure_database_upgrade: polling state=%s version=%s", state, ver)
        if str(state).lower() in ("ready", "succeeded") and ver == target_version:
            final_version = ver
            break
    else:
        raise TimeoutError(f"Azure {engine} {server_name} did not reach {target_version} within {_POLL_TIMEOUT}s")

    return {
        "status": "upgraded",
        "server_name": server_name,
        "resource_group": resource_group,
        "engine": engine,
        "previous_version": current_version,
        "current_version": final_version,
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    server_name = execution_result.get("server_name", parameters.get("server_name", "unknown"))
    engine = execution_result.get("engine", parameters.get("engine", "postgresql"))
    prev = execution_result.get("previous_version", "unknown")
    return {
        "rolled_back": False,
        "reason": (
            f"Azure {engine} flexible server does not support version downgrades. "
            f"Server {server_name} cannot be reverted to version {prev} via API."
        ),
    }
