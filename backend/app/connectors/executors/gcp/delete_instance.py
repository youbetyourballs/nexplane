# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_credentials, get_project_id


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    zone = parameters["zone"]

    if not creds:
        return {
            "deleted": True,
            "instance_name": instance_name,
            "zone": zone,
            "mock": True,
            "pre_state": {},
        }

    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    # 1. Capture pre-state before deletion
    def _get_instance():
        return client.get(project=project, zone=zone, instance=instance_name)

    instance = await loop.run_in_executor(None, _get_instance)

    machine_type_url = instance.machine_type  # full URL
    machine_type = machine_type_url.split("/")[-1] if machine_type_url else "n1-standard-1"

    network_interfaces = [
        {
            "network": ni.network.split("/")[-1] if ni.network else None,
            "subnetwork": ni.subnetwork.split("/")[-1] if ni.subnetwork else None,
        }
        for ni in (instance.network_interfaces or [])
    ]

    metadata_items = {
        item.key: item.value
        for item in (instance.metadata.items if instance.metadata else [])
    }

    tags = list(instance.tags.items) if instance.tags else []
    labels = dict(instance.labels) if instance.labels else {}

    pre_state = {
        "name": instance_name,
        "machine_type": machine_type,
        "zone": zone,
        "project": project,
        "network_interfaces": network_interfaces,
        "metadata": metadata_items,
        "tags": tags,
        "labels": labels,
    }

    # 2. Delete the instance
    def _delete():
        op = client.delete(project=project, zone=zone, instance=instance_name)
        return op.operation.name

    op_name = await loop.run_in_executor(None, _delete)
    return {
        "deleted": True,
        "instance_name": instance_name,
        "zone": zone,
        "project": project,
        "operation": op_name,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    from google.cloud import compute_v1

    credentials = get_credentials(creds)
    project = pre_state.get("project") or get_project_id(creds)
    zone = pre_state.get("zone") or execution_result.get("zone") or parameters.get("zone")
    instance_name = pre_state.get("name") or execution_result.get("instance_name") or parameters.get("instance_name")
    machine_type = pre_state.get("machine_type", "n1-standard-1")

    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)

    try:
        def _recreate():
            network_interfaces = []
            for ni in pre_state.get("network_interfaces", []):
                nic = compute_v1.NetworkInterface()
                if ni.get("network"):
                    nic.network = f"projects/{project}/global/networks/{ni['network']}"
                if ni.get("subnetwork"):
                    nic.subnetwork = f"projects/{project}/regions/{zone.rsplit('-', 1)[0]}/subnetworks/{ni['subnetwork']}"
                network_interfaces.append(nic)

            if not network_interfaces:
                nic = compute_v1.NetworkInterface()
                nic.name = "nic0"
                network_interfaces = [nic]

            # Blank boot disk — original disk data is not recoverable
            disk = compute_v1.AttachedDisk()
            disk.boot = True
            disk.auto_delete = True
            disk.initialize_params = compute_v1.AttachedDiskInitializeParams(
                source_image="projects/debian-cloud/global/images/family/debian-12",
                disk_size_gb=20,
            )

            instance_body = compute_v1.Instance(
                name=instance_name,
                machine_type=f"zones/{zone}/machineTypes/{machine_type}",
                network_interfaces=network_interfaces,
                disks=[disk],
            )

            if pre_state.get("labels"):
                instance_body.labels = pre_state["labels"]

            if pre_state.get("tags"):
                instance_body.tags = compute_v1.Tags(items=pre_state["tags"])

            if pre_state.get("metadata"):
                items = [
                    compute_v1.Items(key=k, value=v)
                    for k, v in pre_state["metadata"].items()
                ]
                instance_body.metadata = compute_v1.Metadata(items=items)

            op = client.insert(project=project, zone=zone, instance_resource=instance_body)
            return op.operation.name

        op_name = await loop.run_in_executor(None, _recreate)
        return {
            "rolled_back": True,
            "instance_name": instance_name,
            "zone": zone,
            "operation": op_name,
            "note": "Instance recreated with blank boot disk — original boot disk data is not restored",
        }
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
