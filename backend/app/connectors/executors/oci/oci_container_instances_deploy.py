# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""OCI Container Instances deploy executor.

Deploys a new container image by deleting the existing container instance
and creating a new one. OCI Container Instances have no revision model,
so there is a brief unavailability gap during the swap.

Rollback: delete the new instance and recreate the old one (partial —
same unavailability gap applies, and the old instance ID is no longer valid).
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "partial"

_POLL_INTERVAL = 10
_POLL_TIMEOUT = 300


async def _run(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def _wait_active(creds: dict, instance_id: str) -> str:
    deadline = time.time() + _POLL_TIMEOUT
    while time.time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL)

        def _poll():
            import oci
            from app.connectors.executors.oci._client import get_oci_config
            config = get_oci_config(creds)
            client = oci.container_instances.ContainerInstanceClient(config)
            return client.get_container_instance(instance_id).data

        inst = await _run(_poll)
        logger.info("oci_container_instances_deploy: polling id=%s state=%s", instance_id, inst.lifecycle_state)
        if inst.lifecycle_state == "ACTIVE":
            return inst.id
        if inst.lifecycle_state in ("FAILED", "DELETED"):
            raise RuntimeError(f"OCI Container Instance {instance_id} reached state {inst.lifecycle_state}")
    raise TimeoutError(f"OCI Container Instance {instance_id} did not become ACTIVE within {_POLL_TIMEOUT}s")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = connector.credentials
    compartment_id = parameters["compartment_id"]
    instance_id = parameters["instance_id"]
    image = parameters["image"]
    display_name = parameters.get("display_name", "nexplane-container-instance")

    def _get():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.container_instances.ContainerInstanceClient(config)
        return client.get_container_instance(instance_id).data

    old_instance = await _run(_get)
    previous_image = old_instance.containers[0].image_url if old_instance.containers else ""
    old_snapshot = {
        "availability_domain": old_instance.availability_domain,
        "shape": old_instance.shape,
        "image": previous_image,
        "display_name": old_instance.display_name,
    }

    # Delete old instance
    def _delete():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.container_instances.ContainerInstanceClient(config)
        client.delete_container_instance(instance_id)

    await _run(_delete)
    logger.info("oci_container_instances_deploy: deleted old instance %s", instance_id)

    # Create new instance
    def _create():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.container_instances.ContainerInstanceClient(config)
        details = oci.container_instances.models.CreateContainerInstanceDetails(
            compartment_id=compartment_id,
            availability_domain=old_snapshot["availability_domain"],
            shape=old_snapshot["shape"],
            display_name=display_name,
            containers=[oci.container_instances.models.CreateContainerDetails(image_url=image)],
        )
        return client.create_container_instance(details).data

    new_instance = await _run(_create)
    new_instance_id = new_instance.id
    logger.info("oci_container_instances_deploy: created new instance %s", new_instance_id)

    await _wait_active(creds, new_instance_id)

    return {
        "status": "deployed",
        "previous_instance_id": instance_id,
        "new_instance_id": new_instance_id,
        "compartment_id": compartment_id,
        "previous_image": previous_image,
        "current_image": image,
        "old_snapshot": old_snapshot,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = connector.credentials
    compartment_id = execution_result["compartment_id"]
    new_instance_id = execution_result["new_instance_id"]
    old_snapshot = execution_result["old_snapshot"]

    # Delete new instance
    def _delete_new():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.container_instances.ContainerInstanceClient(config)
        client.delete_container_instance(new_instance_id)

    await _run(_delete_new)
    logger.info("oci_container_instances_deploy rollback: deleted new instance %s", new_instance_id)

    # Recreate with original image
    def _recreate():
        import oci
        from app.connectors.executors.oci._client import get_oci_config
        config = get_oci_config(creds)
        client = oci.container_instances.ContainerInstanceClient(config)
        details = oci.container_instances.models.CreateContainerInstanceDetails(
            compartment_id=compartment_id,
            availability_domain=old_snapshot["availability_domain"],
            shape=old_snapshot["shape"],
            display_name=old_snapshot.get("display_name", "nexplane-container-instance-restored"),
            containers=[oci.container_instances.models.CreateContainerDetails(image_url=old_snapshot["image"])],
        )
        return client.create_container_instance(details).data

    restored = await _run(_recreate)
    await _wait_active(creds, restored.id)
    logger.info("oci_container_instances_deploy rollback: restored instance %s", restored.id)

    return {
        "rolled_back": True,
        "restored_instance_id": restored.id,
        "restored_image": old_snapshot["image"],
        "note": "Brief unavailability occurred during delete/recreate swap.",
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
