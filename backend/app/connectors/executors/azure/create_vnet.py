import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vnet_name = parameters.get("vnet_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    address_prefix = parameters.get("address_prefix", "10.100.0.0/16")
    subnet_prefix = parameters.get("subnet_prefix", "10.100.0.0/24")

    if not creds:
        return {
            "action": "create_vnet",
            "vnet_name": vnet_name,
            "resource_group": rg,
            "location": location,
            "mock": True,
        }

    from ._client import get_network_client
    from azure.mgmt.network.models import VirtualNetwork, AddressSpace, Subnet
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: network.virtual_networks.begin_create_or_update(
            rg, vnet_name,
            VirtualNetwork(
                location=location,
                address_space=AddressSpace(address_prefixes=[address_prefix]),
                subnets=[Subnet(name="default", address_prefix=subnet_prefix)],
            ),
        ).result(),
    )
    return {
        "action": "create_vnet",
        "vnet_name": vnet_name,
        "resource_group": rg,
        "location": location,
        "address_prefix": address_prefix,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_vnet import execute as delete
    return await delete(
        {
            "vnet_name": execution_result.get("vnet_name", parameters.get("vnet_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
