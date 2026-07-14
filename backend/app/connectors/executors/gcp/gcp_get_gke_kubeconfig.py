# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "full"

_MOCK_KUBECONFIG = """\
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: bW9jaw==
    server: https://mock-endpoint
  name: mock-cluster
contexts:
- context:
    cluster: mock-cluster
    user: mock-cluster
  name: mock-cluster
current-context: mock-cluster
kind: Config
preferences: {}
users:
- name: mock-cluster
  user:
    token: mock-token
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    cluster_name = parameters["cluster_name"]
    location = parameters["location"]

    if not creds:
        return {"kubeconfig": _MOCK_KUBECONFIG, "cluster_name": cluster_name, "mock": True}

    from ._client import get_container_client, get_credentials, get_project_id

    project_id = get_project_id(creds)
    client = get_container_client(creds)
    loop = asyncio.get_event_loop()
    cluster_ref = f"projects/{project_id}/locations/{location}/clusters/{cluster_name}"

    cluster = await loop.run_in_executor(None, lambda: client.get_cluster({"name": cluster_ref}))
    endpoint = cluster.endpoint
    ca_cert = cluster.master_auth.cluster_ca_certificate

    credentials_obj = get_credentials(creds)
    from google.auth.transport.requests import Request
    await loop.run_in_executor(None, lambda: credentials_obj.refresh(Request()))
    access_token = credentials_obj.token

    kubeconfig = f"""\
apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {ca_cert}
    server: https://{endpoint}
  name: {cluster_name}
contexts:
- context:
    cluster: {cluster_name}
    user: {cluster_name}
  name: {cluster_name}
current-context: {cluster_name}
kind: Config
preferences: {{}}
users:
- name: {cluster_name}
  user:
    token: {access_token}
"""

    return {"kubeconfig": kubeconfig, "cluster_name": cluster_name, "location": location}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "read-only"}
