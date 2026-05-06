import asyncio
import base64
from datetime import datetime, timezone


_AGENT_STARTUP_SCRIPT = """#!/bin/bash
set -e
NEXPLANE_URL="{nexplane_url}"
NEXPLANE_SECRET="{nexplane_secret}"
VERSION=$(curl -fsSL https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/version)
curl -fsSL "https://nexplane-agent-downloads.s3.us-east-1.amazonaws.com/nexplane-agent-linux-amd64-${{VERSION}}" \\
  -o /usr/local/bin/nexplane-agent
chmod +x /usr/local/bin/nexplane-agent

cat > /etc/systemd/system/nexplane-agent.service <<EOF
[Unit]
Description=Nexplane Agent
After=network.target

[Service]
ExecStart=/usr/local/bin/nexplane-agent --control-plane $NEXPLANE_URL --secret $NEXPLANE_SECRET --mode service
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable nexplane-agent
systemctl start nexplane-agent
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    vm_name = parameters["vm_name"]
    resource_group = parameters["resource_group"]
    location = parameters.get("location", "eastus")
    vm_size = parameters.get("vm_size", "Standard_B1s")
    connection_mode = parameters.get("connection_mode", "agent_extension")
    nexplane_url = parameters.get("nexplane_url", "")
    nexplane_secret = parameters.get("nexplane_secret", "")
    ssh_public_key = parameters.get("ssh_public_key", "")
    admin_password = parameters.get("admin_password", "")
    admin_username = parameters.get("admin_username", "azureuser")
    tailscale_auth_key = parameters.get("tailscale_auth_key", "")

    auto_asset = {
        "name": vm_name,
        "asset_type": "server",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "vm_name": vm_name,
            "resource_group": resource_group,
            "location": location,
            "vm_size": vm_size,
            "connection_mode": connection_mode,
            "os_type": "Linux",
            "provider": "azure",
        },
        "tags": ["azure-vm", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "launch_vm",
            "vm_name": vm_name,
            "resource_group": resource_group,
            "location": location,
            "connection_mode": connection_mode,
            "mock": True,
            "_auto_asset": auto_asset,
        }

    from ._client import get_compute_client, get_network_client
    from azure.mgmt.compute.models import (
        VirtualMachine, HardwareProfile, StorageProfile, OsProfile,
        NetworkProfile, NetworkInterfaceReference, LinuxConfiguration,
        SshConfiguration, SshPublicKey, ImageReference, OSDisk,
        ManagedDiskParameters,
    )
    from azure.mgmt.compute.models import DiskCreateOptionTypes
    from azure.mgmt.network.models import (
        VirtualNetwork, AddressSpace, Subnet, PublicIPAddress,
        PublicIPAddressSku, NetworkInterface, NetworkInterfaceIPConfiguration,
    )

    compute = get_compute_client(creds)
    network = get_network_client(creds)
    loop = asyncio.get_running_loop()

    # Create public IP
    pip = await loop.run_in_executor(
        None,
        lambda: network.public_ip_addresses.begin_create_or_update(
            resource_group, f"{vm_name}-pip",
            PublicIPAddress(
                location=location,
                sku=PublicIPAddressSku(name="Basic"),
                public_ip_allocation_method="Dynamic",
            ),
        ).result(),
    )

    # Create VNet + subnet
    await loop.run_in_executor(
        None,
        lambda: network.virtual_networks.begin_create_or_update(
            resource_group, f"{vm_name}-vnet",
            VirtualNetwork(
                location=location,
                address_space=AddressSpace(address_prefixes=["10.0.0.0/16"]),
                subnets=[Subnet(name="default", address_prefix="10.0.0.0/24")],
            ),
        ).result(),
    )
    subnet = await loop.run_in_executor(
        None, lambda: network.subnets.get(resource_group, f"{vm_name}-vnet", "default")
    )

    # Create NIC
    nic = await loop.run_in_executor(
        None,
        lambda: network.network_interfaces.begin_create_or_update(
            resource_group, f"{vm_name}-nic",
            NetworkInterface(
                location=location,
                ip_configurations=[
                    NetworkInterfaceIPConfiguration(
                        name="ipconfig1",
                        subnet=subnet,
                        public_ip_address=pip,
                    )
                ],
            ),
        ).result(),
    )

    # Build OS profile based on connection_mode
    if connection_mode == "password":
        os_profile = OsProfile(
            computer_name=vm_name,
            admin_username=admin_username,
            admin_password=admin_password,
        )
    else:
        if not ssh_public_key and connection_mode == "ssh":
            raise ValueError("ssh_public_key is required when connection_mode is 'ssh'")
        key_data = ssh_public_key if ssh_public_key else ""
        os_profile = OsProfile(
            computer_name=vm_name,
            admin_username=admin_username,
            linux_configuration=LinuxConfiguration(
                disable_password_authentication=True,
                ssh=SshConfiguration(
                    public_keys=[
                        SshPublicKey(
                            path=f"/home/{admin_username}/.ssh/authorized_keys",
                            key_data=key_data,
                        )
                    ]
                ),
            ),
        )

    # Create VM
    await loop.run_in_executor(
        None,
        lambda: compute.virtual_machines.begin_create_or_update(
            resource_group, vm_name,
            VirtualMachine(
                location=location,
                hardware_profile=HardwareProfile(vm_size=vm_size),
                storage_profile=StorageProfile(
                    image_reference=ImageReference(
                        publisher="Canonical",
                        offer="0001-com-ubuntu-server-jammy",
                        sku="22_04-lts",
                        version="latest",
                    ),
                    os_disk=OSDisk(
                        create_option=DiskCreateOptionTypes.FROM_IMAGE,
                        delete_option="Delete",
                    ),
                ),
                os_profile=os_profile,
                network_profile=NetworkProfile(
                    network_interfaces=[
                        NetworkInterfaceReference(id=nic.id, primary=True)
                    ]
                ),
            ),
        ).result(),
    )

    # Deploy Custom Script Extension for agent_extension mode
    if connection_mode == "agent_extension":
        from azure.mgmt.compute.models import VirtualMachineExtension
        startup_script = _AGENT_STARTUP_SCRIPT.format(
            nexplane_url=nexplane_url,
            nexplane_secret=nexplane_secret,
        )
        if tailscale_auth_key:
            tailscale_prepend = f"""#!/bin/bash
set -e
# Install and join Tailscale first so agent can reach control plane
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --authkey="{tailscale_auth_key}" --hostname="{vm_name}" --accept-routes
"""
            # Remove leading shebang from agent script to avoid duplicate
            agent_body = startup_script.lstrip()
            if agent_body.startswith("#!/bin/bash"):
                agent_body = agent_body[len("#!/bin/bash"):].lstrip()
            startup_script = tailscale_prepend + agent_body
        script_b64 = base64.b64encode(startup_script.encode()).decode()
        await loop.run_in_executor(
            None,
            lambda: compute.virtual_machine_extensions.begin_create_or_update(
                resource_group, vm_name, "NexplaneAgentInstall",
                VirtualMachineExtension(
                    location=location,
                    publisher="Microsoft.Azure.Extensions",
                    type_properties_type="CustomScript",
                    type_handler_version="2.1",
                    auto_upgrade_minor_version=True,
                    settings={"script": script_b64},
                ),
            ).result(),
        )

    # Get public IP (assigned after creation)
    public_ip = ""
    try:
        pip_refreshed = await loop.run_in_executor(
            None, lambda: network.public_ip_addresses.get(resource_group, f"{vm_name}-pip")
        )
        public_ip = pip_refreshed.ip_address or ""
    except Exception:
        pass

    auto_asset["asset_metadata"]["public_ip"] = public_ip

    return {
        "action": "launch_vm",
        "vm_name": vm_name,
        "resource_group": resource_group,
        "location": location,
        "connection_mode": connection_mode,
        "public_ip": public_ip,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.terminate_vm import execute as terminate
    return await terminate(
        {
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
            "vm_name": execution_result.get("vm_name", parameters.get("vm_name")),
        },
        [], connector,
    )
