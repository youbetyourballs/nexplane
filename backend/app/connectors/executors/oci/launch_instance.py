import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    compartment_id = parameters.get("compartment_id", "")
    subnet_id = parameters.get("subnet_id", "")
    name = parameters.get("name", "nexplane-oci-instance")
    mode = parameters.get("mode", "quick")
    os_choice = parameters.get("os", "oracle_linux")
    shape = parameters.get("shape", "VM.Standard.E2.1.Micro")
    ocpus = parameters.get("ocpus", 1)
    memory_in_gbs = parameters.get("memory_in_gbs", 1)
    image_id = parameters.get("image_id", "")
    key_pair_id = parameters.get("key_pair_id", "")
    ssh_public_key = parameters.get("ssh_public_key", "")

    auto_asset = {
        "name": name,
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "instance_id": "",
            "shape": shape,
            "region": creds.get("region", "us-ashburn-1") if creds else "us-ashburn-1",
            "compartment_id": compartment_id,
            "lifecycle_state": "PROVISIONING",
            "provider": "oci",
        },
        "tags": ["oci", "compute", "nexplane-managed"],
    }

    if not creds:
        auto_asset["asset_metadata"]["instance_id"] = "ocid1.instance.oc1..mock"
        auto_asset["asset_metadata"]["private_ip"] = "10.0.0.10"
        return {
            "action": "launch_instance",
            "instance_id": "ocid1.instance.oc1..mock",
            "shape": shape,
            "private_ip": "10.0.0.10",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_compute_client, get_network_client
    import oci

    region = creds["region"]
    tenancy_id = creds["tenancy"]

    if not compartment_id:
        compartment_id = tenancy_id

    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Resolve subnet_id
    if not subnet_id:
        subnets = await loop.run_in_executor(
            None,
            lambda: network.list_subnets(compartment_id).data,
        )
        available = [s for s in subnets if s.lifecycle_state == "AVAILABLE"]
        if not available:
            raise ValueError(
                "No available subnet in this compartment. "
                "Run oci_vcn_create -> oci_subnet_create first."
            )
        subnet_id = available[0].id

    # Resolve image_id
    if not image_id or mode == "quick":
        os_map = {
            "oracle_linux": "Oracle Linux",
            "ubuntu": "Canonical Ubuntu",
        }
        os_filter = os_map.get(os_choice, "Oracle Linux")

        images = await loop.run_in_executor(
            None,
            lambda: compute.list_images(
                compartment_id,
                operating_system=os_filter,
                shape=shape,
                sort_by="TIMECREATED",
                sort_order="DESC",
            ).data,
        )
        platform_images = [img for img in images if img.base_image_id is None]
        if not platform_images:
            raise ValueError(
                f"No platform image found for OS '{os_filter}' and shape '{shape}' in region '{region}'."
            )
        image_id = platform_images[0].id

    # Resolve SSH public key from key_pair asset
    if not ssh_public_key and key_pair_id:
        try:
            from app.database import AsyncSessionLocal
            from sqlalchemy import select
            from app.models.asset import Asset
            import uuid
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Asset).where(Asset.id == uuid.UUID(key_pair_id))
                )
                kp_asset = result.scalar_one_or_none()
                if kp_asset:
                    ssh_public_key = (kp_asset.asset_metadata or {}).get("public_key", "")
        except Exception:
            pass

    metadata = {}
    if ssh_public_key:
        metadata["ssh_authorized_keys"] = ssh_public_key

    # Only flex shapes accept shape_config — fixed shapes (E2.1.Micro etc.) reject it
    is_flex = "Flex" in shape

    # Resolve availability domains — try each until one succeeds (quota may be 0 in some ADs)
    from ._client import get_identity_client
    identity = get_identity_client(creds)
    ads = await loop.run_in_executor(
        None,
        lambda: identity.list_availability_domains(compartment_id).data,
    )

    instance_id = None
    last_error = None
    for ad in ads:
        launch_kwargs = dict(
            compartment_id=compartment_id,
            display_name=name,
            shape=shape,
            availability_domain=ad.name,
            image_id=image_id,
            subnet_id=subnet_id,
            metadata=metadata,
            freeform_tags={"managed-by": "nexplane"},
        )
        if is_flex:
            launch_kwargs["shape_config"] = oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=float(ocpus),
                memory_in_gbs=float(memory_in_gbs),
            )

        details = oci.core.models.LaunchInstanceDetails(**launch_kwargs)
        try:
            response = await loop.run_in_executor(
                None,
                lambda d=details: compute.launch_instance(d).data,
            )
            instance_id = response.id
            break
        except Exception as exc:
            last_error = exc
            continue

    if not instance_id:
        raise RuntimeError(f"Failed to launch instance in any availability domain: {last_error}")

    # Wait for RUNNING (wait_instance_state created in Task 6)
    from .wait_instance_state import execute as wait
    await wait(
        {"instance_id": instance_id, "target_state": "RUNNING"},
        [],
        connector,
    )

    # Fetch private IP
    private_ip = None
    try:
        vnic_attachments = await loop.run_in_executor(
            None,
            lambda: compute.list_vnic_attachments(
                compartment_id=compartment_id, instance_id=instance_id
            ).data,
        )
        if vnic_attachments:
            net2 = get_network_client(creds)
            vnic = await loop.run_in_executor(
                None,
                lambda va=vnic_attachments[0]: net2.get_vnic(va.vnic_id).data,
            )
            private_ip = vnic.private_ip
    except Exception:
        pass

    auto_asset["asset_metadata"]["instance_id"] = instance_id
    auto_asset["asset_metadata"]["private_ip"] = private_ip
    auto_asset["asset_metadata"]["lifecycle_state"] = "RUNNING"

    return {
        "action": "launch_instance",
        "instance_id": instance_id,
        "shape": shape,
        "private_ip": private_ip,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .terminate_instance import execute as terminate
    instance_id = execution_result.get("instance_id") or parameters.get("instance_id", "")
    return await terminate(
        {"instance_id": instance_id, "preserve_boot_volume": False},
        [],
        connector,
    )
